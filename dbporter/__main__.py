import os
import pickle
import tempfile

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

import config


class SourceDB(object):
    def __init__(self):
        self.engine = None
        self.use_view = None
        self.name = None


source_dbs = {}

dest_db = create_engine(config.DATABASES['dest']['url'], echo=False)
dest_db_name = config.DATABASES['dest']['name']
for db in config.DATABASES['sources']:
    source_db = SourceDB()
    source_db.engine = create_engine(db['url'], echo=False)
    source_db.use_view = db.get('use_view', False)
    source_db.name = db.get('name')
    source_dbs[db['name']] = source_db

_path = os.path.join(tempfile.gettempdir(), 'ROMULAN_TOOLS_TEMP_FILE')


def load_successful_tables():
    try:
        with open(_path) as fo:
            return pickle.load(fo)
    except IOError:
        return None


_successful_tables = []


def insert_successful_table(table_name):
    _successful_tables.append(table_name)


def dump_failed_tables():
    with open(_path, 'w') as fo:
        pickle.dump(_successful_tables, fo)


def truncate(names):
    if names:
        print '[TRUNCATE TABLE IN {}]: {}'.format(dest_db_name.upper(),
                                                  ', '.join(names))
        sql = """
        SET FOREIGN_KEY_CHECKS = 0;
        {}
        SET FOREIGN_KEY_CHECKS = 1;
        """.format('\n'.join('TRUNCATE {};'.format(name) for name in names))
        dest_db.execute(sql)


def create_view(name, sdb, sql):
    print '[CREATE VIEW]: {}.{}'.format(sdb.name.upper(), name)
    sql_ = """
    IF exists(SELECT *
        FROM sysobjects
            WHERE name = '{name}')
          BEGIN
            DROP VIEW {name}
          END
    """.format(name=name)
    sdb.engine.execute(text(sql_).execution_options(autocommit=True))
    sql_ = """
    CREATE VIEW {name} AS
    ({sql})
    """.format(name=name, sql=sql)
    sdb.engine.execute(sql_)


def main(refresh=True):
    # if refresh is True, insert all tables into destination database
    # if refresh is False, only insert table of which the last migration was failed
    if refresh:
        orders = config.ORDERS
        print 'FRESH\n'
    else:
        successful_orders = load_successful_tables()
        if successful_orders is None:
            orders = config.ORDERS
            print 'FRESH\n'
        else:
            successful_orders = set(successful_orders)
            orders = (name for name in config.ORDERS if
                      name not in successful_orders)
            print 'NOT FRESH\n'

    # truncate tables in destination database
    truncate(orders + config.INITIALS)

    # insert
    for name in orders:
        sdb, sql = get_sdb(name), get_sdb_sql(name)
        if sdb.use_view:
            create_view(name, sdb, sql)
        insert(sdb, sql, name)

    dump_failed_tables()

    # execute initial SQL in destination database
    for name in config.INITIALS:
        print '[EXECUTE INITIAL SQL SCRIPT IN {}]: {}.sql'.format(
            dest_db_name.upper(), name)
        sql = get_ddb_sql(name)
        dest_db.execute(sql)


def get_sdb(name):
    dir_, _ = get_db_name_and_sql_path(name)
    return source_dbs[dir_]


def get_sdb_sql(name):
    _, sql_path = get_db_name_and_sql_path(name)
    with open(sql_path) as fo:
        return fo.read()


def get_ddb_sql(name):
    _, sql_path = get_db_name_and_sql_path(name, from_source=False)
    with open(sql_path) as fo:
        return fo.read()


_name_file_mapping = None


def get_db_name_and_sql_path(name, from_source=True):
    global _name_file_mapping
    if _name_file_mapping is None:
        _name_file_mapping = {
            'sources': {},
            'dest': {}
        }
        for basedir, dirs, filenames in os.walk(config.ROOT_DIR):
            for filename in filenames:
                root, ext = os.path.splitext(filename)
                if ext == '.sql':
                    dir_ = os.path.basename(basedir)
                    if dir_ in source_dbs:
                        _name_file_mapping['sources'][root] = (
                            dir_,
                            os.path.join(basedir, filename))
                    elif dir_ == config.DATABASES['dest']['name']:
                        _name_file_mapping['dest'][root] = (
                            dir_,
                            os.path.join(basedir, filename))
                    else:
                        pass

    if from_source:
        return _name_file_mapping['sources'][name]
    else:
        return _name_file_mapping['dest'][name]


def insert(sdb, sql, name):
    print '[INSERT DATA]: {from_}.{name} -> {into}.{name}'.format(
        from_=sdb.name, into=dest_db_name, name=name)
    try:
        data = pd.read_sql(sql, sdb.engine)
        data.to_sql(name=name, con=dest_db, if_exists='append', index=False,
                    chunksize=5000)
        insert_successful_table(name)
    except SQLAlchemyError as e:
        print '\n{char:-^80}\n{msg}\n{char:-^80}\n'.format(char='-', msg=e)


if __name__ == '__main__':
    main(refresh=True)