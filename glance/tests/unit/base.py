# Copyright 2012 OpenStack Foundation.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import os
import shutil

import fixtures
from oslo.config import cfg
from oslo.db import options

from glance.common import exception
from glance.openstack.common import jsonutils
from glance import store
from glance.store import location
from glance.store import sheepdog
from glance.store import vmware_datastore
from glance.tests import stubs
from glance.tests import utils as test_utils

CONF = cfg.CONF
CONF.import_opt('filesystem_store_datadir', 'glance.store.filesystem')


class StoreClearingUnitTest(test_utils.BaseTestCase):

    def setUp(self):
        super(StoreClearingUnitTest, self).setUp()
        # Ensure stores + locations cleared
        location.SCHEME_TO_CLS_MAP = {}

        self._create_stores()
        self.addCleanup(setattr, location, 'SCHEME_TO_CLS_MAP', dict())

    def _create_stores(self, passing_config=True):
        """Create known stores. Mock out sheepdog's subprocess dependency
        on collie.

        :param passing_config: making store driver passes basic configurations.
        :returns: the number of how many store drivers been loaded.
        """

        def _fun(*args, **kwargs):
            if passing_config:
                return None
            else:
                raise exception.BadStoreConfiguration()

        self.stubs.Set(sheepdog.Store, 'configure', _fun)
        self.stubs.Set(vmware_datastore.Store, 'configure', _fun)
        self.stubs.Set(vmware_datastore.Store, 'configure_add', _fun)
        return store.create_stores()


class IsolatedUnitTest(StoreClearingUnitTest):

    """
    Unit test case that establishes a mock environment within
    a testing directory (in isolation)
    """
    registry = None

    def setUp(self):
        super(IsolatedUnitTest, self).setUp()
        self.test_dir = self.useFixture(fixtures.TempDir()).path
        policy_file = self._copy_data_file('policy.json', self.test_dir)
        options.set_defaults(CONF, connection='sqlite://',
                             sqlite_db='glance.sqlite')
        self.config(verbose=False,
                    debug=False,
                    default_store='filesystem',
                    filesystem_store_datadir=os.path.join(self.test_dir),
                    policy_file=policy_file,
                    lock_path=os.path.join(self.test_dir))
        stubs.stub_out_registry_and_store_server(self.stubs,
                                                 self.test_dir,
                                                 registry=self.registry)

    def _copy_data_file(self, file_name, dst_dir):
        src_file_name = os.path.join('glance/tests/etc', file_name)
        shutil.copy(src_file_name, dst_dir)
        dst_file_name = os.path.join(dst_dir, file_name)
        return dst_file_name

    def set_policy_rules(self, rules):
        fap = open(CONF.policy_file, 'w')
        fap.write(jsonutils.dumps(rules))
        fap.close()
