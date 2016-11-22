"""Unit tests for Superset"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging
import json
import os
import unittest

from flask_appbuilder.security.sqla import models as ab_models

from superset import app, cli, db, models, appbuilder, sm
from superset.security import sync_role_definitions

os.environ['SUPERSET_CONFIG'] = 'tests.superset_test_config'

BASE_DIR = app.config.get("BASE_DIR")


class SupersetTestCase(unittest.TestCase):
    requires_examples = False
    examples_loaded = False

    def __init__(self, *args, **kwargs):
        if (
                self.requires_examples and
                not os.environ.get('SOLO_TEST') and
                not os.environ.get('examples_loaded')
            ):
            logging.info("Loading examples")
            cli.load_examples(load_test_data=True)
            logging.info("Done loading examples")
            sync_role_definitions()
            os.environ['examples_loaded'] = '1'
        else:
            sync_role_definitions()
        super(SupersetTestCase, self).__init__(*args, **kwargs)
        self.client = app.test_client()
        self.maxDiff = None

        gamma_sqllab = sm.add_role("gamma_sqllab")
        for perm in sm.find_role('Gamma').permissions:
            sm.add_permission_role(gamma_sqllab, perm)
        for perm in sm.find_role('sql_lab').permissions:
            sm.add_permission_role(gamma_sqllab, perm)

        admin = appbuilder.sm.find_user('admin')
        if not admin:
            appbuilder.sm.add_user(
                'admin', 'admin', ' user', 'admin@fab.org',
                appbuilder.sm.find_role('Admin'),
                password='general')

        gamma = appbuilder.sm.find_user('gamma')
        if not gamma:
            appbuilder.sm.add_user(
                'gamma', 'gamma', 'user', 'gamma@fab.org',
                appbuilder.sm.find_role('Gamma'),
                password='general')

        gamma_sqllab = appbuilder.sm.find_user('gamma_sqllab')
        if not gamma_sqllab:
            gamma_sqllab = appbuilder.sm.add_user(
                'gamma_sqllab', 'gamma_sqllab', 'user', 'gamma_sqllab@fab.org',
                appbuilder.sm.find_role('gamma_sqllab'),
                password='general')

        alpha = appbuilder.sm.find_user('alpha')
        if not alpha:
            appbuilder.sm.add_user(
                'alpha', 'alpha', 'user', 'alpha@fab.org',
                appbuilder.sm.find_role('Alpha'),
                password='general')

        # create druid cluster and druid datasources
        session = db.session
        cluster = session.query(models.DruidCluster).filter_by(
            cluster_name="druid_test").first()
        if not cluster:
            cluster = models.DruidCluster(cluster_name="druid_test")
            session.add(cluster)
            session.commit()

            druid_datasource1 = models.DruidDatasource(
                datasource_name='druid_ds_1',
                cluster_name='druid_test'
            )
            session.add(druid_datasource1)
            druid_datasource2 = models.DruidDatasource(
                datasource_name='druid_ds_2',
                cluster_name='druid_test'
            )
            session.add(druid_datasource2)
            session.commit()


    def get_or_create(self, cls, criteria, session):
        obj = session.query(cls).filter_by(**criteria).first()
        if not obj:
            obj = cls(**criteria)
        return obj

    def login(self, username='admin', password='general'):
        resp = self.get_resp(
            '/login/',
            data=dict(username=username, password=password))
        self.assertIn('Welcome', resp)

    def get_query_by_sql(self, sql):
        session = db.create_scoped_session()
        query = session.query(models.Query).filter_by(sql=sql).first()
        session.close()
        return query

    def get_latest_query(self, sql):
        session = db.create_scoped_session()
        query = (
            session.query(models.Query)
            .order_by(models.Query.id.desc())
            .first()
        )
        session.close()
        return query

    def get_slice(self, slice_name, session):
        slc = (
            session.query(models.Slice)
            .filter_by(slice_name=slice_name)
            .one()
        )
        session.expunge_all()
        return slc

    def get_table_by_name(self, name):
        return db.session.query(models.SqlaTable).filter_by(
            table_name=name).first()

    def get_druid_ds_by_name(self, name):
        return db.session.query(models.DruidDatasource).filter_by(
            datasource_name=name).first()

    def get_resp(self, url, data=None, follow_redirects=True):
        """Shortcut to get the parsed results while following redirects"""
        if data:
            resp = self.client.post(
                url, data=data, follow_redirects=follow_redirects)
            return resp.data.decode('utf-8')
        else:
            resp = self.client.get(url, follow_redirects=follow_redirects)
            return resp.data.decode('utf-8')

    def get_json_resp(self, url, data=None):
        """Shortcut to get the parsed results while following redirects"""
        resp = self.get_resp(url, data=data)
        return json.loads(resp)

    def get_main_database(self, session):
        return (
            db.session.query(models.Database)
            .filter_by(database_name='main')
            .first()
        )

    def get_access_requests(self, username, ds_type, ds_id):
            DAR = models.DatasourceAccessRequest
            return (
                db.session.query(DAR)
                .filter(
                    DAR.created_by == sm.find_user(username=username),
                    DAR.datasource_type == ds_type,
                    DAR.datasource_id == ds_id,
                )
                .first()
            )

    def logout(self):
        self.client.get('/logout/', follow_redirects=True)

    def grant_public_access_to_table(self, table):
        public_role = appbuilder.sm.find_role('Public')
        perms = db.session.query(ab_models.PermissionView).all()
        for perm in perms:
            if (perm.permission.name == 'datasource_access' and
                    perm.view_menu and table.perm in perm.view_menu.name):
                appbuilder.sm.add_permission_role(public_role, perm)

    def revoke_public_access_to_table(self, table):
        public_role = appbuilder.sm.find_role('Public')
        perms = db.session.query(ab_models.PermissionView).all()
        for perm in perms:
            if (perm.permission.name == 'datasource_access' and
                    perm.view_menu and table.perm in perm.view_menu.name):
                appbuilder.sm.del_permission_role(public_role, perm)

    def run_sql(self, sql, client_id, user_name=None):
        if user_name:
            self.logout()
            self.login(username=(user_name if user_name else 'admin'))
        dbid = self.get_main_database(db.session).id
        resp = self.get_json_resp(
            '/superset/sql_json/',
            data=dict(database_id=dbid, sql=sql, select_as_create_as=False,
                      client_id=client_id),
        )
        return resp
