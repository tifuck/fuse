"""Isolated pooled MariaDB races; requires local DBA socket for test schema only."""
import os
import unittest
from urllib.parse import urlsplit,urlunsplit
import pymysql
from app.config import settings
from app import db
from app.mysql_compat import connect


def main():
    schema=f'fuse_consistency_test_{os.getpid()}'
    admin=pymysql.connect(user='root',unix_socket='/run/mysqld/mysqld.sock',autocommit=True)
    url=urlsplit(settings.database_url)
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE `{schema}`')
        c.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=%s',(url.path.strip('/'),))
        for (table,) in c.fetchall():
            c.execute(f'CREATE TABLE `{schema}`.`{table}` LIKE `{url.path.strip("/")}`.`{table}`')
        for host in ('localhost','127.0.0.1'):
            c.execute(f'GRANT ALL ON `{schema}`.* TO %s@%s',(url.username,host))
    pool=connect(urlunsplit(url._replace(path='/'+schema)),max_connections=6)
    db._conn=pool
    try:
        suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_stream_consistency')
        return not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
    finally:
        db._conn=None;pool.close()
        with admin.cursor() as c:
            c.execute(f'DROP DATABASE `{schema}`')
            for host in ('localhost','127.0.0.1'):
                c.execute(f'REVOKE ALL ON `{schema}`.* FROM %s@%s',(url.username,host))
        admin.close()


if __name__=='__main__':raise SystemExit(main())
