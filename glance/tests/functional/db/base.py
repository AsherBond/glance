# Copyright 2010-2012 OpenStack Foundation
# Copyright 2012 Justin Santa Barbara
# Copyright 2013 IBM Corp.
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

import copy
import datetime
import functools
from unittest import mock
import uuid

from oslo_db import exception as db_exception
from oslo_db.sqlalchemy import utils as sqlalchemyutils
from oslo_utils import timeutils as oslo_timeutils
from sqlalchemy import sql

from glance.common import exception
from glance.common import timeutils
from glance import context
from glance.db.sqlalchemy import api as db_api
from glance.db.sqlalchemy import models
import glance.tests.functional.db as db_tests
from glance.tests import utils as test_utils


# The default sort order of results is whatever sort key is specified,
# plus created_at and id for ties.  When we're not specifying a sort_key,
# we get the default (created_at). Some tests below expect the fixtures to be
# returned in array-order, so if the created_at timestamps are the same,
# these tests rely on the UUID* values being in order
UUID1, UUID2, UUID3 = sorted([str(uuid.uuid4()) for x in range(3)])


def build_image_fixture(**kwargs):
    default_datetime = oslo_timeutils.utcnow()
    image = {
        'id': str(uuid.uuid4()),
        'name': 'fake image #2',
        'status': 'active',
        'disk_format': 'vhd',
        'container_format': 'ovf',
        'is_public': True,
        'created_at': default_datetime,
        'updated_at': default_datetime,
        'deleted_at': None,
        'deleted': False,
        'checksum': None,
        'min_disk': 5,
        'min_ram': 256,
        'size': 19,
        'locations': [{'url': "file:///tmp/glance-tests/2",
                       'metadata': {}, 'status': 'active'}],
        'properties': {},
    }
    if 'visibility' in kwargs:
        image.pop('is_public')
    image.update(kwargs)
    return image


def build_task_fixture(**kwargs):
    default_datetime = oslo_timeutils.utcnow()
    task = {
        'id': str(uuid.uuid4()),
        'type': 'import',
        'status': 'pending',
        'input': {'ping': 'pong'},
        'owner': str(uuid.uuid4()),
        'message': None,
        'expires_at': None,
        'created_at': default_datetime,
        'updated_at': default_datetime,
    }
    task.update(kwargs)
    return task


class TestDriver(test_utils.BaseTestCase):

    def setUp(self):
        super(TestDriver, self).setUp()
        context_cls = context.RequestContext
        self.adm_context = context_cls(is_admin=True,
                                       auth_token='user:user:admin')
        self.context = context_cls(is_admin=False,
                                   auth_token='user:user:user')
        self.db_api = db_tests.get_db(self.config)
        db_tests.reset_db(self.db_api)
        self.fixtures = self.build_image_fixtures()
        self.create_images(self.fixtures)

    def build_image_fixtures(self):
        dt1 = oslo_timeutils.utcnow()
        dt2 = dt1 + datetime.timedelta(microseconds=5)
        fixtures = [
            {
                'id': UUID1,
                'created_at': dt1,
                'updated_at': dt1,
                'properties': {'foo': 'bar', 'far': 'boo'},
                'protected': True,
                'size': 13,
            },
            {
                'id': UUID2,
                'created_at': dt1,
                'updated_at': dt2,
                'size': 17,
            },
            {
                'id': UUID3,
                'created_at': dt2,
                'updated_at': dt2,
            },
        ]
        return [build_image_fixture(**fixture) for fixture in fixtures]

    def create_images(self, images):
        for fixture in images:
            self.db_api.image_create(self.adm_context, fixture)


class DriverTests(object):

    def test_image_create_requires_status(self):
        fixture = {'name': 'mark', 'size': 12}
        self.assertRaises(exception.Invalid,
                          self.db_api.image_create, self.context, fixture)
        fixture = {'name': 'mark', 'size': 12, 'status': 'queued'}
        self.db_api.image_create(self.context, fixture)

    @mock.patch.object(oslo_timeutils, 'utcnow')
    def test_image_create_defaults(self, mock_utcnow):
        mock_utcnow.return_value = datetime.datetime.now(
            datetime.timezone.utc).replace(tzinfo=None)
        create_time = oslo_timeutils.utcnow()
        values = {'status': 'queued',
                  'created_at': create_time,
                  'updated_at': create_time}
        image = self.db_api.image_create(self.context, values)

        self.assertIsNone(image['name'])
        self.assertIsNone(image['container_format'])
        self.assertEqual(0, image['min_ram'])
        self.assertEqual(0, image['min_disk'])
        self.assertIsNone(image['owner'])
        self.assertEqual('shared', image['visibility'])
        self.assertIsNone(image['size'])
        self.assertIsNone(image['checksum'])
        self.assertIsNone(image['disk_format'])
        self.assertEqual([], image['locations'])
        self.assertFalse(image['protected'])
        self.assertFalse(image['deleted'])
        self.assertIsNone(image['deleted_at'])
        self.assertEqual([], image['properties'])
        self.assertEqual(create_time, image['created_at'])
        self.assertEqual(create_time, image['updated_at'])

        # Image IDs aren't predictable, but they should be populated
        self.assertTrue(uuid.UUID(image['id']))

        # NOTE(bcwaldon): the tags attribute should not be returned as a part
        # of a core image entity
        self.assertNotIn('tags', image)

    def test_image_create_duplicate_id(self):
        self.assertRaises(exception.Duplicate,
                          self.db_api.image_create,
                          self.context, {'id': UUID1, 'status': 'queued'})

    def test_image_create_with_locations(self):
        locations = [{'url': 'a', 'metadata': {}, 'status': 'active'},
                     {'url': 'b', 'metadata': {}, 'status': 'active'}]

        fixture = {'status': 'queued',
                   'locations': locations}
        image = self.db_api.image_create(self.context, fixture)
        actual = [{'url': location['url'], 'metadata': location['metadata'],
                   'status': location['status']}
                  for location in image['locations']]
        self.assertEqual(locations, actual)

    def test_image_create_without_locations(self):
        locations = []
        fixture = {'status': 'queued',
                   'locations': locations}
        self.db_api.image_create(self.context, fixture)

    def test_image_create_with_location_data(self):
        location_data = [{'url': 'a', 'metadata': {'key': 'value'},
                          'status': 'active'},
                         {'url': 'b', 'metadata': {},
                          'status': 'active'}]
        fixture = {'status': 'queued', 'locations': location_data}
        image = self.db_api.image_create(self.context, fixture)
        actual = [{'url': location['url'], 'metadata': location['metadata'],
                   'status': location['status']}
                  for location in image['locations']]
        self.assertEqual(location_data, actual)

    def test_image_create_properties(self):
        fixture = {'status': 'queued', 'properties': {'ping': 'pong'}}
        image = self.db_api.image_create(self.context, fixture)
        expected = [{'name': 'ping', 'value': 'pong'}]
        actual = [{'name': p['name'], 'value': p['value']}
                  for p in image['properties']]
        self.assertEqual(expected, actual)

    def test_image_create_unknown_attributes(self):
        fixture = {'ping': 'pong'}
        self.assertRaises(exception.Invalid,
                          self.db_api.image_create, self.context, fixture)

    def test_image_create_bad_name(self):
        bad_name = 'A name with forbidden symbol \U0001f62a'
        fixture = {'name': bad_name, 'size': 12, 'status': 'queued'}
        self.assertRaises(exception.Invalid, self.db_api.image_create,
                          self.context, fixture)

    def test_image_create_bad_checksum(self):
        # checksum should be no longer than 32 characters
        bad_checksum = "42" * 42
        fixture = {'checksum': bad_checksum}
        self.assertRaises(exception.Invalid, self.db_api.image_create,
                          self.context, fixture)
        # if checksum is not longer than 32 characters but non-ascii ->
        # still raise 400
        fixture = {'checksum': '\u042f' * 32}
        self.assertRaises(exception.Invalid, self.db_api.image_create,
                          self.context, fixture)

    def test_image_create_bad_int_params(self):
        int_too_long = 2 ** 31 + 42
        for param in ['min_disk', 'min_ram']:
            fixture = {param: int_too_long}
            self.assertRaises(exception.Invalid, self.db_api.image_create,
                              self.context, fixture)

    def test_image_create_bad_property(self):
        # bad value
        fixture = {'status': 'queued',
                   'properties': {'bad': 'Bad \U0001f62a'}}
        self.assertRaises(exception.Invalid, self.db_api.image_create,
                          self.context, fixture)
        # bad property names are also not allowed
        fixture = {'status': 'queued', 'properties': {'Bad \U0001f62a': 'ok'}}
        self.assertRaises(exception.Invalid, self.db_api.image_create,
                          self.context, fixture)

    def test_image_create_bad_location(self):
        location_data = [{'url': 'a', 'metadata': {'key': 'value'},
                          'status': 'active'},
                         {'url': 'Bad \U0001f60a', 'metadata': {},
                          'status': 'active'}]
        fixture = {'status': 'queued', 'locations': location_data}
        self.assertRaises(exception.Invalid, self.db_api.image_create,
                          self.context, fixture)

    def test_image_update_core_attribute(self):
        fixture = {'status': 'queued'}
        image = self.db_api.image_update(self.adm_context, UUID3, fixture)
        self.assertEqual('queued', image['status'])
        self.assertNotEqual(image['created_at'], image['updated_at'])

    def test_image_update_with_locations(self):
        locations = [{'url': 'a', 'metadata': {}, 'status': 'active'},
                     {'url': 'b', 'metadata': {}, 'status': 'active'}]
        fixture = {'locations': locations}
        image = self.db_api.image_update(self.adm_context, UUID3, fixture)
        self.assertEqual(2, len(image['locations']))
        self.assertIn('id', image['locations'][0])
        self.assertIn('id', image['locations'][1])
        image['locations'][0].pop('id')
        image['locations'][1].pop('id')
        self.assertEqual(locations, image['locations'])

    def test_image_update_with_location_data(self):
        location_data = [{'url': 'a', 'metadata': {'key': 'value'},
                          'status': 'active'},
                         {'url': 'b', 'metadata': {}, 'status': 'active'}]
        fixture = {'locations': location_data}
        image = self.db_api.image_update(self.adm_context, UUID3, fixture)
        self.assertEqual(2, len(image['locations']))
        self.assertIn('id', image['locations'][0])
        self.assertIn('id', image['locations'][1])
        image['locations'][0].pop('id')
        image['locations'][1].pop('id')
        self.assertEqual(location_data, image['locations'])

    def test_image_update(self):
        fixture = {'status': 'queued', 'properties': {'ping': 'pong'}}
        image = self.db_api.image_update(self.adm_context, UUID3, fixture)
        expected = [{'name': 'ping', 'value': 'pong'}]
        actual = [{'name': p['name'], 'value': p['value']}
                  for p in image['properties']]
        self.assertEqual(expected, actual)
        self.assertEqual('queued', image['status'])
        self.assertNotEqual(image['created_at'], image['updated_at'])

    def test_image_update_properties(self):
        fixture = {'properties': {'ping': 'pong'}}
        image = self.db_api.image_update(self.adm_context, UUID1, fixture)
        expected = {'ping': 'pong', 'foo': 'bar', 'far': 'boo'}
        actual = {p['name']: p['value'] for p in image['properties']}
        self.assertEqual(expected, actual)
        self.assertNotEqual(image['created_at'], image['updated_at'])

    def test_image_update_purge_properties(self):
        fixture = {'properties': {'ping': 'pong'}}
        image = self.db_api.image_update(self.adm_context, UUID1,
                                         fixture, purge_props=True)
        properties = {p['name']: p for p in image['properties']}

        # New properties are set
        self.assertIn('ping', properties)
        self.assertEqual('pong', properties['ping']['value'])
        self.assertFalse(properties['ping']['deleted'])

        # Original properties still show up, but with deleted=True
        # TODO(markwash): db api should not return deleted properties
        self.assertIn('foo', properties)
        self.assertEqual('bar', properties['foo']['value'])
        self.assertTrue(properties['foo']['deleted'])

    def test_image_update_bad_name(self):
        fixture = {'name': 'A new name with forbidden symbol \U0001f62a'}
        self.assertRaises(exception.Invalid, self.db_api.image_update,
                          self.adm_context, UUID1, fixture)

    def test_image_update_bad_property(self):
        # bad value
        fixture = {'status': 'queued',
                   'properties': {'bad': 'Bad \U0001f62a'}}
        self.assertRaises(exception.Invalid, self.db_api.image_update,
                          self.adm_context, UUID1, fixture)
        # bad property names are also not allowed
        fixture = {'status': 'queued', 'properties': {'Bad \U0001f62a': 'ok'}}
        self.assertRaises(exception.Invalid, self.db_api.image_update,
                          self.adm_context, UUID1, fixture)

    def test_image_update_bad_location(self):
        location_data = [{'url': 'a', 'metadata': {'key': 'value'},
                          'status': 'active'},
                         {'url': 'Bad \U0001f60a', 'metadata': {},
                          'status': 'active'}]
        fixture = {'status': 'queued', 'locations': location_data}
        self.assertRaises(exception.Invalid, self.db_api.image_update,
                          self.adm_context, UUID1, fixture)

    def test_update_locations_direct(self):
        """
        For some reasons update_locations can be called directly
        (not via image_update), so better check that everything is ok if passed
        4 byte unicode characters
        """
        # update locations correctly first to retrieve existing location id
        location_data = [{'url': 'a', 'metadata': {'key': 'value'},
                          'status': 'active'}]
        fixture = {'locations': location_data}
        image = self.db_api.image_update(self.adm_context, UUID1, fixture)
        self.assertEqual(1, len(image['locations']))
        self.assertIn('id', image['locations'][0])
        loc_id = image['locations'][0].pop('id')
        bad_location = {'url': 'Bad \U0001f60a', 'metadata': {},
                        'status': 'active', 'id': loc_id}
        self.assertRaises(exception.Invalid,
                          self.db_api.image_location_update,
                          self.adm_context, UUID1, bad_location)

    def test_image_property_delete(self):
        fixture = {'name': 'ping', 'value': 'pong', 'image_id': UUID1}
        prop = self.db_api.image_property_create(self.context, fixture)
        prop = self.db_api.image_property_delete(self.context,
                                                 prop['name'], UUID1)
        self.assertIsNotNone(prop['deleted_at'])
        self.assertTrue(prop['deleted'])

    def test_image_get(self):
        image = self.db_api.image_get(self.context, UUID1)
        self.assertEqual(self.fixtures[0]['id'], image['id'])

    def test_image_get_disallow_deleted(self):
        self.db_api.image_destroy(self.adm_context, UUID1)
        self.assertRaises(exception.NotFound, self.db_api.image_get,
                          self.context, UUID1)

    def test_image_get_allow_deleted(self):
        self.db_api.image_destroy(self.adm_context, UUID1)
        image = self.db_api.image_get(self.adm_context, UUID1)
        self.assertEqual(self.fixtures[0]['id'], image['id'])
        self.assertTrue(image['deleted'])

    def test_image_get_force_allow_deleted(self):
        self.db_api.image_destroy(self.adm_context, UUID1)
        image = self.db_api.image_get(self.context, UUID1,
                                      force_show_deleted=True)
        self.assertEqual(self.fixtures[0]['id'], image['id'])

    def test_image_get_not_owned(self):
        TENANT1 = str(uuid.uuid4())
        TENANT2 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        ctxt2 = context.RequestContext(is_admin=False, tenant=TENANT2,
                                       auth_token='user:%s:user' % TENANT2)
        image = self.db_api.image_create(
            ctxt1, {'status': 'queued', 'owner': TENANT1})
        self.assertRaises(exception.Forbidden,
                          self.db_api.image_get, ctxt2, image['id'])

    def test_image_get_not_found(self):
        UUID = str(uuid.uuid4())
        self.assertRaises(exception.NotFound,
                          self.db_api.image_get, self.context, UUID)

    def test_image_get_all(self):
        images = self.db_api.image_get_all(self.context)
        self.assertEqual(3, len(images))

    def test_image_get_all_with_filter(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={
                                               'id': self.fixtures[0]['id'],
                                           })
        self.assertEqual(1, len(images))
        self.assertEqual(self.fixtures[0]['id'], images[0]['id'])

    def test_image_get_all_with_filter_user_defined_property(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'foo': 'bar'})
        self.assertEqual(1, len(images))
        self.assertEqual(self.fixtures[0]['id'], images[0]['id'])

    def test_image_get_all_with_filter_nonexistent_userdef_property(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'faz': 'boo'})
        self.assertEqual(0, len(images))

    def test_image_get_all_with_filter_userdef_prop_nonexistent_value(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'foo': 'baz'})
        self.assertEqual(0, len(images))

    def test_image_get_all_with_filter_multiple_user_defined_properties(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'foo': 'bar',
                                                    'far': 'boo'})
        self.assertEqual(1, len(images))
        self.assertEqual(images[0]['id'], self.fixtures[0]['id'])

    def test_image_get_all_with_filter_nonexistent_user_defined_property(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'foo': 'bar',
                                                    'faz': 'boo'})
        self.assertEqual(0, len(images))

    def test_image_get_all_with_filter_user_deleted_property(self):
        fixture = {'name': 'poo', 'value': 'bear', 'image_id': UUID1}
        prop = self.db_api.image_property_create(self.context,
                                                 fixture)

        images = self.db_api.image_get_all(self.context,
                                           filters={
                                               'properties': {'poo': 'bear'},
                                           })
        self.assertEqual(1, len(images))
        self.db_api.image_property_delete(self.context,
                                          prop['name'], images[0]['id'])
        images = self.db_api.image_get_all(self.context,
                                           filters={
                                               'properties': {'poo': 'bear'},
                                           })
        self.assertEqual(0, len(images))

    def test_image_get_all_with_filter_undefined_property(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'poo': 'bear'})
        self.assertEqual(0, len(images))

    def test_image_get_all_with_filter_protected(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'protected':
                                                    True})
        self.assertEqual(1, len(images))
        images = self.db_api.image_get_all(self.context,
                                           filters={'protected':
                                                    False})
        self.assertEqual(2, len(images))

    def test_image_get_all_with_filter_comparative_created_at(self):
        anchor = timeutils.isotime(self.fixtures[0]['created_at'])
        time_expr = 'lt:' + anchor

        images = self.db_api.image_get_all(self.context,
                                           filters={'created_at': time_expr})
        self.assertEqual(0, len(images))

    def test_image_get_all_with_filter_comparative_updated_at(self):
        anchor = timeutils.isotime(self.fixtures[0]['updated_at'])
        time_expr = 'lt:' + anchor

        images = self.db_api.image_get_all(self.context,
                                           filters={'updated_at': time_expr})
        self.assertEqual(0, len(images))

    def test_filter_image_by_invalid_operator(self):
        self.assertRaises(exception.InvalidFilterOperatorValue,
                          self.db_api.image_get_all,
                          self.context, filters={'status': 'lala:active'})

    def test_image_get_all_with_filter_in_status(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'status': 'in:active'})
        self.assertEqual(3, len(images))

    def test_image_get_all_with_filter_in_name(self):
        data = 'in:%s' % self.fixtures[0]['name']
        images = self.db_api.image_get_all(self.context,
                                           filters={'name': data})
        self.assertEqual(3, len(images))

    def test_image_get_all_with_filter_in_container_format(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'container_format':
                                                    'in:ami,bare,ovf'})
        self.assertEqual(3, len(images))

    def test_image_get_all_with_filter_in_disk_format(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'disk_format':
                                                    'in:vhd'})
        self.assertEqual(3, len(images))

    def test_image_get_all_with_filter_in_id(self):
        data = 'in:%s,%s' % (UUID1, UUID2)
        images = self.db_api.image_get_all(self.context,
                                           filters={'id': data})
        self.assertEqual(2, len(images))

    def test_image_get_all_with_quotes(self):
        fixture = {'name': 'fake\\\"name'}
        self.db_api.image_update(self.adm_context, UUID3, fixture)

        fixture = {'name': 'fake,name'}
        self.db_api.image_update(self.adm_context, UUID2, fixture)

        fixture = {'name': 'fakename'}
        self.db_api.image_update(self.adm_context, UUID1, fixture)

        data = 'in:\"fake\\\"name\",fakename,\"fake,name\"'

        images = self.db_api.image_get_all(self.context,
                                           filters={'name': data})
        self.assertEqual(3, len(images))

    def test_image_get_all_with_invalid_quotes(self):
        invalid_expr = ['in:\"name', 'in:\"name\"name', 'in:name\"dd\"',
                        'in:na\"me', 'in:\"name\"\"name\"']
        for expr in invalid_expr:
            self.assertRaises(exception.InvalidParameterValue,
                              self.db_api.image_get_all,
                              self.context, filters={'name': expr})

    def test_image_get_all_size_min_max(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={
                                               'size_min': 10,
                                               'size_max': 15,
                                           })
        self.assertEqual(1, len(images))
        self.assertEqual(self.fixtures[0]['id'], images[0]['id'])

    def test_image_get_all_size_min(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'size_min': 15})
        self.assertEqual(2, len(images))
        self.assertEqual(self.fixtures[2]['id'], images[0]['id'])
        self.assertEqual(self.fixtures[1]['id'], images[1]['id'])

    def test_image_get_all_size_range(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'size_max': 15,
                                                    'size_min': 20})
        self.assertEqual(0, len(images))

    def test_image_get_all_size_max(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'size_max': 15})
        self.assertEqual(1, len(images))
        self.assertEqual(self.fixtures[0]['id'], images[0]['id'])

    def test_image_get_all_with_filter_min_range_bad_value(self):
        self.assertRaises(exception.InvalidFilterRangeValue,
                          self.db_api.image_get_all,
                          self.context, filters={'size_min': 'blah'})

    def test_image_get_all_with_filter_max_range_bad_value(self):
        self.assertRaises(exception.InvalidFilterRangeValue,
                          self.db_api.image_get_all,
                          self.context, filters={'size_max': 'blah'})

    def test_image_get_all_marker(self):
        images = self.db_api.image_get_all(self.context, marker=UUID3)
        self.assertEqual(2, len(images))

    def test_image_get_all_marker_with_size(self):
        # Use sort_key=size to test BigInteger
        images = self.db_api.image_get_all(self.context, sort_key=['size'],
                                           marker=UUID3)
        self.assertEqual(2, len(images))
        self.assertEqual(17, images[0]['size'])
        self.assertEqual(13, images[1]['size'])

    def test_image_get_all_marker_deleted(self):
        """Cannot specify a deleted image as a marker."""
        self.db_api.image_destroy(self.adm_context, UUID1)
        filters = {'deleted': False}
        self.assertRaises(exception.NotFound, self.db_api.image_get_all,
                          self.context, marker=UUID1, filters=filters)

    def test_image_get_all_marker_deleted_showing_deleted_as_admin(self):
        """Specify a deleted image as a marker if showing deleted images."""
        self.db_api.image_destroy(self.adm_context, UUID3)
        images = self.db_api.image_get_all(self.adm_context, marker=UUID3)
        # NOTE(bcwaldon): an admin should see all images (deleted or not)
        self.assertEqual(2, len(images))

    def test_image_get_all_marker_deleted_showing_deleted(self):
        """Specify a deleted image as a marker if showing deleted images.

        A non-admin user has to explicitly ask for deleted
        images, and should only see deleted images in the results
        """
        self.db_api.image_destroy(self.adm_context, UUID3)
        self.db_api.image_destroy(self.adm_context, UUID1)
        filters = {'deleted': True}
        images = self.db_api.image_get_all(self.context, marker=UUID3,
                                           filters=filters)
        self.assertEqual(1, len(images))

    def test_image_get_all_marker_null_name_desc(self):
        """Check an image with name null is handled

        Check an image with name null is handled
        marker is specified and order is descending
        """
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        UUIDX = str(uuid.uuid4())
        self.db_api.image_create(ctxt1, {'id': UUIDX,
                                         'status': 'queued',
                                         'name': None,
                                         'owner': TENANT1})

        images = self.db_api.image_get_all(ctxt1, marker=UUIDX,
                                           sort_key=['name'],
                                           sort_dir=['desc'])
        image_ids = [image['id'] for image in images]
        expected = []
        self.assertEqual(sorted(expected), sorted(image_ids))

    def test_image_get_all_marker_null_disk_format_desc(self):
        """Check an image with disk_format null is handled

        Check an image with disk_format null is handled when
        marker is specified and order is descending
        """
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        UUIDX = str(uuid.uuid4())
        self.db_api.image_create(ctxt1, {'id': UUIDX,
                                         'status': 'queued',
                                         'disk_format': None,
                                         'owner': TENANT1})

        images = self.db_api.image_get_all(ctxt1, marker=UUIDX,
                                           sort_key=['disk_format'],
                                           sort_dir=['desc'])
        image_ids = [image['id'] for image in images]
        expected = []
        self.assertEqual(sorted(expected), sorted(image_ids))

    def test_image_get_all_marker_null_container_format_desc(self):
        """Check an image with container_format null is handled

        Check an image with container_format null is handled when
        marker is specified and order is descending
        """
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        UUIDX = str(uuid.uuid4())
        self.db_api.image_create(ctxt1, {'id': UUIDX,
                                         'status': 'queued',
                                         'container_format': None,
                                         'owner': TENANT1})

        images = self.db_api.image_get_all(ctxt1, marker=UUIDX,
                                           sort_key=['container_format'],
                                           sort_dir=['desc'])
        image_ids = [image['id'] for image in images]
        expected = []
        self.assertEqual(sorted(expected), sorted(image_ids))

    def test_image_get_all_marker_null_name_asc(self):
        """Check an image with name null is handled

        Check an image with name null is handled when
        marker is specified and order is ascending
        """
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        UUIDX = str(uuid.uuid4())
        self.db_api.image_create(ctxt1, {'id': UUIDX,
                                         'status': 'queued',
                                         'name': None,
                                         'owner': TENANT1})

        images = self.db_api.image_get_all(ctxt1, marker=UUIDX,
                                           sort_key=['name'],
                                           sort_dir=['asc'])
        image_ids = [image['id'] for image in images]
        expected = [UUID3, UUID2, UUID1]
        self.assertEqual(sorted(expected), sorted(image_ids))

    def test_image_get_all_marker_null_disk_format_asc(self):
        """Check an image with disk_format null is handled

        Check an image with disk_format null is handled when
        marker is specified and order is ascending
        """
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        UUIDX = str(uuid.uuid4())
        self.db_api.image_create(ctxt1, {'id': UUIDX,
                                         'status': 'queued',
                                         'disk_format': None,
                                         'owner': TENANT1})

        images = self.db_api.image_get_all(ctxt1, marker=UUIDX,
                                           sort_key=['disk_format'],
                                           sort_dir=['asc'])
        image_ids = [image['id'] for image in images]
        expected = [UUID3, UUID2, UUID1]
        self.assertEqual(sorted(expected), sorted(image_ids))

    def test_image_get_all_marker_null_container_format_asc(self):
        """Check an image with container_format null is handled

        Check an image with container_format null is handled when
        marker is specified and order is ascending
        """
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        UUIDX = str(uuid.uuid4())
        self.db_api.image_create(ctxt1, {'id': UUIDX,
                                         'status': 'queued',
                                         'container_format': None,
                                         'owner': TENANT1})

        images = self.db_api.image_get_all(ctxt1, marker=UUIDX,
                                           sort_key=['container_format'],
                                           sort_dir=['asc'])
        image_ids = [image['id'] for image in images]
        expected = [UUID3, UUID2, UUID1]
        self.assertEqual(sorted(expected), sorted(image_ids))

    def test_image_get_all_limit(self):
        images = self.db_api.image_get_all(self.context, limit=2)
        self.assertEqual(2, len(images))

        # A limit of None should not equate to zero
        images = self.db_api.image_get_all(self.context, limit=None)
        self.assertEqual(3, len(images))

        # A limit of zero should actually mean zero
        images = self.db_api.image_get_all(self.context, limit=0)
        self.assertEqual(0, len(images))

    def test_image_get_all_owned(self):
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False,
                                       tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        UUIDX = str(uuid.uuid4())
        image_meta_data = {'id': UUIDX, 'status': 'queued', 'owner': TENANT1}
        self.db_api.image_create(ctxt1, image_meta_data)

        TENANT2 = str(uuid.uuid4())
        ctxt2 = context.RequestContext(is_admin=False,
                                       tenant=TENANT2,
                                       auth_token='user:%s:user' % TENANT2)
        UUIDY = str(uuid.uuid4())
        image_meta_data = {'id': UUIDY, 'status': 'queued', 'owner': TENANT2}
        self.db_api.image_create(ctxt2, image_meta_data)

        images = self.db_api.image_get_all(ctxt1)

        image_ids = [image['id'] for image in images]
        expected = [UUIDX, UUID3, UUID2, UUID1]
        self.assertEqual(sorted(expected), sorted(image_ids))

    def test_image_get_all_owned_checksum(self):
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False,
                                       tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        UUIDX = str(uuid.uuid4())
        CHECKSUM1 = '91264c3edf5972c9f1cb309543d38a5c'
        image_meta_data = {
            'id': UUIDX,
            'status': 'queued',
            'checksum': CHECKSUM1,
            'owner': TENANT1
        }
        self.db_api.image_create(ctxt1, image_meta_data)
        image_member_data = {
            'image_id': UUIDX,
            'member': TENANT1,
            'can_share': False,
            "status": "accepted",
        }
        self.db_api.image_member_create(ctxt1, image_member_data)

        TENANT2 = str(uuid.uuid4())
        ctxt2 = context.RequestContext(is_admin=False,
                                       tenant=TENANT2,
                                       auth_token='user:%s:user' % TENANT2)
        UUIDY = str(uuid.uuid4())
        CHECKSUM2 = '92264c3edf5972c9f1cb309543d38a5c'
        image_meta_data = {
            'id': UUIDY,
            'status': 'queued',
            'checksum': CHECKSUM2,
            'owner': TENANT2
        }
        self.db_api.image_create(ctxt2, image_meta_data)
        image_member_data = {
            'image_id': UUIDY,
            'member': TENANT2,
            'can_share': False,
            "status": "accepted",
        }
        self.db_api.image_member_create(ctxt2, image_member_data)

        filters = {'visibility': 'shared', 'checksum': CHECKSUM2}
        images = self.db_api.image_get_all(ctxt2, filters)

        self.assertEqual(1, len(images))
        self.assertEqual(UUIDY, images[0]['id'])

    def test_image_get_all_with_filter_tags(self):
        self.db_api.image_tag_create(self.context, UUID1, 'x86')
        self.db_api.image_tag_create(self.context, UUID1, '64bit')
        self.db_api.image_tag_create(self.context, UUID2, 'power')
        self.db_api.image_tag_create(self.context, UUID2, '64bit')
        images = self.db_api.image_get_all(self.context,
                                           filters={'tags': ['64bit']})
        self.assertEqual(2, len(images))
        image_ids = [image['id'] for image in images]
        expected = [UUID1, UUID2]
        self.assertEqual(sorted(expected), sorted(image_ids))

    def test_image_get_all_with_filter_multi_tags(self):
        self.db_api.image_tag_create(self.context, UUID1, 'x86')
        self.db_api.image_tag_create(self.context, UUID1, '64bit')
        self.db_api.image_tag_create(self.context, UUID2, 'power')
        self.db_api.image_tag_create(self.context, UUID2, '64bit')
        images = self.db_api.image_get_all(self.context,
                                           filters={'tags': ['64bit', 'power']
                                                    })
        self.assertEqual(1, len(images))
        self.assertEqual(UUID2, images[0]['id'])

    def test_image_get_all_with_filter_tags_and_nonexistent(self):
        self.db_api.image_tag_create(self.context, UUID1, 'x86')
        images = self.db_api.image_get_all(self.context,
                                           filters={'tags': ['x86', 'fake']
                                                    })
        self.assertEqual(0, len(images))

    def test_image_get_all_with_filter_deleted_tags(self):
        tag = self.db_api.image_tag_create(self.context, UUID1, 'AIX')
        images = self.db_api.image_get_all(self.context,
                                           filters={
                                               'tags': [tag],
                                           })
        self.assertEqual(1, len(images))
        self.db_api.image_tag_delete(self.context, UUID1, tag)
        images = self.db_api.image_get_all(self.context,
                                           filters={
                                               'tags': [tag],
                                           })
        self.assertEqual(0, len(images))

    def test_image_get_all_with_filter_undefined_tags(self):
        images = self.db_api.image_get_all(self.context,
                                           filters={'tags': ['fake']})
        self.assertEqual(0, len(images))

    def test_image_paginate(self):
        """Paginate through a list of images using limit and marker"""
        now = oslo_timeutils.utcnow()
        extra_uuids = [(str(uuid.uuid4()),
                        now + datetime.timedelta(seconds=i * 5))
                       for i in range(2)]
        extra_images = [build_image_fixture(id=_id,
                                            created_at=_dt,
                                            updated_at=_dt)
                        for _id, _dt in extra_uuids]
        self.create_images(extra_images)

        # Reverse uuids to match default sort of created_at
        extra_uuids.reverse()

        page = self.db_api.image_get_all(self.context, limit=2)
        self.assertEqual([i[0] for i in extra_uuids], [i['id'] for i in page])
        last = page[-1]['id']

        page = self.db_api.image_get_all(self.context, limit=2, marker=last)
        self.assertEqual([UUID3, UUID2], [i['id'] for i in page])

        page = self.db_api.image_get_all(self.context, limit=2, marker=UUID2)
        self.assertEqual([UUID1], [i['id'] for i in page])

    def test_image_get_all_invalid_sort_key(self):
        self.assertRaises(exception.InvalidSortKey, self.db_api.image_get_all,
                          self.context, sort_key=['blah'])

    def test_image_get_all_limit_marker(self):
        images = self.db_api.image_get_all(self.context, limit=2)
        self.assertEqual(2, len(images))

    def test_image_get_all_with_tag_returning(self):
        expected_tags = {UUID1: ['foo'], UUID2: ['bar'], UUID3: ['baz']}

        self.db_api.image_tag_create(self.context, UUID1,
                                     expected_tags[UUID1][0])
        self.db_api.image_tag_create(self.context, UUID2,
                                     expected_tags[UUID2][0])
        self.db_api.image_tag_create(self.context, UUID3,
                                     expected_tags[UUID3][0])

        images = self.db_api.image_get_all(self.context, return_tag=True)
        self.assertEqual(3, len(images))

        for image in images:
            self.assertIn('tags', image)
            self.assertEqual(expected_tags[image['id']], image['tags'])

        self.db_api.image_tag_delete(self.context, UUID1,
                                     expected_tags[UUID1][0])
        expected_tags[UUID1] = []

        images = self.db_api.image_get_all(self.context, return_tag=True)
        self.assertEqual(3, len(images))

        for image in images:
            self.assertIn('tags', image)
            self.assertEqual(expected_tags[image['id']], image['tags'])

    def test_image_destroy(self):
        location_data = [{'url': 'a', 'metadata': {'key': 'value'},
                          'status': 'active'},
                         {'url': 'b', 'metadata': {},
                          'status': 'active'}]
        fixture = {'status': 'queued', 'locations': location_data}
        image = self.db_api.image_create(self.context, fixture)
        IMG_ID = image['id']

        fixture = {'name': 'ping', 'value': 'pong', 'image_id': IMG_ID}
        prop = self.db_api.image_property_create(self.context, fixture)
        TENANT2 = str(uuid.uuid4())
        fixture = {'image_id': IMG_ID, 'member': TENANT2, 'can_share': False}
        member = self.db_api.image_member_create(self.context, fixture)
        self.db_api.image_tag_create(self.context, IMG_ID, 'snarf')

        self.assertEqual(2, len(image['locations']))
        self.assertIn('id', image['locations'][0])
        self.assertIn('id', image['locations'][1])
        image['locations'][0].pop('id')
        image['locations'][1].pop('id')
        self.assertEqual(location_data, image['locations'])
        self.assertEqual(('ping', 'pong', IMG_ID, False),
                         (prop['name'], prop['value'],
                          prop['image_id'], prop['deleted']))
        self.assertEqual((TENANT2, IMG_ID, False),
                         (member['member'], member['image_id'],
                          member['can_share']))
        self.assertEqual(['snarf'],
                         self.db_api.image_tag_get_all(self.context, IMG_ID))

        image = self.db_api.image_destroy(self.adm_context, IMG_ID)
        self.assertTrue(image['deleted'])
        self.assertTrue(image['deleted_at'])
        self.assertRaises(exception.NotFound, self.db_api.image_get,
                          self.context, IMG_ID)

        self.assertEqual([], image['locations'])
        prop = image['properties'][0]
        self.assertEqual(('ping', IMG_ID, True),
                         (prop['name'], prop['image_id'], prop['deleted']))
        self.context.auth_token = 'user:%s:user' % TENANT2
        members = self.db_api.image_member_find(self.context, IMG_ID)
        self.assertEqual([], members)
        tags = self.db_api.image_tag_get_all(self.context, IMG_ID)
        self.assertEqual([], tags)

    def test_image_destroy_with_delete_all(self):
        """Check the image child element's _image_delete_all methods.

        checks if all the image_delete_all methods deletes only the child
        elements of the image to be deleted.
        """
        TENANT2 = str(uuid.uuid4())
        location_data = [{'url': 'a', 'metadata': {'key': 'value'},
                          'status': 'active'},
                         {'url': 'b', 'metadata': {}, 'status': 'active'}]

        def _create_image_with_child_entries():
            fixture = {'status': 'queued', 'locations': location_data}

            image_id = self.db_api.image_create(self.context, fixture)['id']

            fixture = {'name': 'ping', 'value': 'pong', 'image_id': image_id}
            self.db_api.image_property_create(self.context, fixture)
            fixture = {'image_id': image_id, 'member': TENANT2,
                       'can_share': False}
            self.db_api.image_member_create(self.context, fixture)
            self.db_api.image_tag_create(self.context, image_id, 'snarf')
            return image_id

        ACTIVE_IMG_ID = _create_image_with_child_entries()
        DEL_IMG_ID = _create_image_with_child_entries()

        deleted_image = self.db_api.image_destroy(self.adm_context, DEL_IMG_ID)
        self.assertTrue(deleted_image['deleted'])
        self.assertTrue(deleted_image['deleted_at'])
        self.assertRaises(exception.NotFound, self.db_api.image_get,
                          self.context, DEL_IMG_ID)

        active_image = self.db_api.image_get(self.context, ACTIVE_IMG_ID)
        self.assertFalse(active_image['deleted'])
        self.assertFalse(active_image['deleted_at'])

        self.assertEqual(2, len(active_image['locations']))
        self.assertIn('id', active_image['locations'][0])
        self.assertIn('id', active_image['locations'][1])
        active_image['locations'][0].pop('id')
        active_image['locations'][1].pop('id')
        self.assertEqual(location_data, active_image['locations'])
        self.assertEqual(1, len(active_image['properties']))
        prop = active_image['properties'][0]
        self.assertEqual(('ping', 'pong', ACTIVE_IMG_ID),
                         (prop['name'], prop['value'],
                          prop['image_id']))
        self.assertEqual((False, None),
                         (prop['deleted'], prop['deleted_at']))
        self.context.auth_token = 'user:%s:user' % TENANT2
        members = self.db_api.image_member_find(self.context, ACTIVE_IMG_ID)
        self.assertEqual(1, len(members))
        member = members[0]
        self.assertEqual((TENANT2, ACTIVE_IMG_ID, False),
                         (member['member'], member['image_id'],
                          member['can_share']))
        tags = self.db_api.image_tag_get_all(self.context, ACTIVE_IMG_ID)
        self.assertEqual(['snarf'], tags)

    def test_image_get_multiple_members(self):
        TENANT1 = str(uuid.uuid4())
        TENANT2 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        ctxt2 = context.RequestContext(is_admin=False, tenant=TENANT2,
                                       auth_token='user:%s:user' % TENANT2)
        UUIDX = str(uuid.uuid4())
        # We need a shared image and context.owner should not match image
        # owner
        self.db_api.image_create(ctxt1, {'id': UUIDX,
                                         'status': 'queued',
                                         'is_public': False,
                                         'owner': TENANT1})
        values = {'image_id': UUIDX, 'member': TENANT2, 'can_share': False}
        self.db_api.image_member_create(ctxt1, values)

        image = self.db_api.image_get(ctxt2, UUIDX)
        self.assertEqual(UUIDX, image['id'])

        # by default get_all displays only images with status 'accepted'
        images = self.db_api.image_get_all(ctxt2)
        self.assertEqual(3, len(images))

        # filter by rejected
        images = self.db_api.image_get_all(ctxt2, member_status='rejected')
        self.assertEqual(3, len(images))

        # filter by visibility
        images = self.db_api.image_get_all(ctxt2,
                                           filters={'visibility': 'shared'})
        self.assertEqual(0, len(images))

        # filter by visibility
        images = self.db_api.image_get_all(ctxt2, member_status='pending',
                                           filters={'visibility': 'shared'})
        self.assertEqual(1, len(images))

        # filter by visibility
        images = self.db_api.image_get_all(ctxt2, member_status='all',
                                           filters={'visibility': 'shared'})
        self.assertEqual(1, len(images))

        # filter by status pending
        images = self.db_api.image_get_all(ctxt2, member_status='pending')
        self.assertEqual(4, len(images))

        # filter by status all
        images = self.db_api.image_get_all(ctxt2, member_status='all')
        self.assertEqual(4, len(images))

    def test_is_image_visible(self):
        TENANT1 = str(uuid.uuid4())
        TENANT2 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False, tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)
        ctxt2 = context.RequestContext(is_admin=False, tenant=TENANT2,
                                       auth_token='user:%s:user' % TENANT2)
        UUIDX = str(uuid.uuid4())
        # We need a shared image and context.owner should not match image
        # owner
        image = self.db_api.image_create(ctxt1, {'id': UUIDX,
                                                 'status': 'queued',
                                                 'is_public': False,
                                                 'owner': TENANT1})

        values = {'image_id': UUIDX, 'member': TENANT2, 'can_share': False}
        self.db_api.image_member_create(ctxt1, values)

        result = self.db_api.is_image_visible(ctxt2, image)
        self.assertTrue(result)

        # image should not be visible for a deleted member
        members = self.db_api.image_member_find(ctxt1, image_id=UUIDX)
        self.db_api.image_member_delete(ctxt1, members[0]['id'])

        result = self.db_api.is_image_visible(ctxt2, image)
        self.assertFalse(result)

    def test_is_community_image_visible(self):
        TENANT1 = str(uuid.uuid4())
        TENANT2 = str(uuid.uuid4())
        owners_ctxt = context.RequestContext(is_admin=False, tenant=TENANT1,
                                             auth_token='user:%s:user'
                                             % TENANT1)
        viewing_ctxt = context.RequestContext(is_admin=False, user=TENANT2,
                                              auth_token='user:%s:user'
                                              % TENANT2)
        UUIDX = str(uuid.uuid4())
        # We need a community image and context.owner should not match image
        # owner
        image = self.db_api.image_create(owners_ctxt,
                                         {'id': UUIDX,
                                          'status': 'queued',
                                          'visibility': 'community',
                                          'owner': TENANT1})

        # image should be visible in every context
        result = self.db_api.is_image_visible(owners_ctxt, image)
        self.assertTrue(result)

        result = self.db_api.is_image_visible(viewing_ctxt, image)
        self.assertTrue(result)

    def test_image_tag_create(self):
        tag = self.db_api.image_tag_create(self.context, UUID1, 'snap')
        self.assertEqual('snap', tag)

    def test_image_tag_create_bad_value(self):
        self.assertRaises(exception.Invalid,
                          self.db_api.image_tag_create, self.context,
                          UUID1, 'Bad \U0001f62a')

    def test_image_tag_set_all(self):
        tags = self.db_api.image_tag_get_all(self.context, UUID1)
        self.assertEqual([], tags)

        self.db_api.image_tag_set_all(self.context, UUID1, ['ping', 'pong'])

        tags = self.db_api.image_tag_get_all(self.context, UUID1)
        # NOTE(bcwaldon): tag ordering should match exactly what was provided
        self.assertEqual(['ping', 'pong'], tags)

    def test_image_tag_get_all(self):
        self.db_api.image_tag_create(self.context, UUID1, 'snap')
        self.db_api.image_tag_create(self.context, UUID1, 'snarf')
        self.db_api.image_tag_create(self.context, UUID2, 'snarf')

        # Check the tags for the first image
        tags = self.db_api.image_tag_get_all(self.context, UUID1)
        expected = ['snap', 'snarf']
        self.assertEqual(expected, tags)

        # Check the tags for the second image
        tags = self.db_api.image_tag_get_all(self.context, UUID2)
        expected = ['snarf']
        self.assertEqual(expected, tags)

    def test_image_tag_get_all_no_tags(self):
        actual = self.db_api.image_tag_get_all(self.context, UUID1)
        self.assertEqual([], actual)

    def test_image_tag_get_all_non_existent_image(self):
        bad_image_id = str(uuid.uuid4())
        actual = self.db_api.image_tag_get_all(self.context, bad_image_id)
        self.assertEqual([], actual)

    def test_image_tag_delete(self):
        self.db_api.image_tag_create(self.context, UUID1, 'snap')
        self.db_api.image_tag_delete(self.context, UUID1, 'snap')
        self.assertRaises(exception.NotFound, self.db_api.image_tag_delete,
                          self.context, UUID1, 'snap')

    @mock.patch.object(oslo_timeutils, 'utcnow')
    def test_image_member_create(self, mock_utcnow):
        mock_utcnow.return_value = datetime.datetime.now(
            datetime.timezone.utc).replace(tzinfo=None)
        memberships = self.db_api.image_member_find(self.context)
        self.assertEqual([], memberships)

        TENANT1 = str(uuid.uuid4())
        # NOTE(flaper87): Update auth token, otherwise
        # non visible members won't be returned.
        self.context.auth_token = 'user:%s:user' % TENANT1
        self.db_api.image_member_create(self.context,
                                        {'member': TENANT1, 'image_id': UUID1})

        memberships = self.db_api.image_member_find(self.context)
        self.assertEqual(1, len(memberships))
        actual = memberships[0]
        self.assertIsNotNone(actual['created_at'])
        self.assertIsNotNone(actual['updated_at'])
        actual.pop('id')
        actual.pop('created_at')
        actual.pop('updated_at')
        expected = {
            'member': TENANT1,
            'image_id': UUID1,
            'can_share': False,
            'status': 'pending',
            'deleted': False,
        }
        self.assertEqual(expected, actual)

    def test_image_member_update(self):
        TENANT1 = str(uuid.uuid4())

        # NOTE(flaper87): Update auth token, otherwise
        # non visible members won't be returned.
        self.context.auth_token = 'user:%s:user' % TENANT1
        member = self.db_api.image_member_create(self.context,
                                                 {'member': TENANT1,
                                                  'image_id': UUID1})
        member_id = member.pop('id')
        member.pop('created_at')
        member.pop('updated_at')

        expected = {'member': TENANT1,
                    'image_id': UUID1,
                    'status': 'pending',
                    'can_share': False,
                    'deleted': False}
        self.assertEqual(expected, member)

        member = self.db_api.image_member_update(self.context,
                                                 member_id,
                                                 {'can_share': True})

        self.assertNotEqual(member['created_at'], member['updated_at'])
        member.pop('id')
        member.pop('created_at')
        member.pop('updated_at')
        expected = {'member': TENANT1,
                    'image_id': UUID1,
                    'status': 'pending',
                    'can_share': True,
                    'deleted': False}
        self.assertEqual(expected, member)

        members = self.db_api.image_member_find(self.context,
                                                member=TENANT1,
                                                image_id=UUID1)
        member = members[0]
        member.pop('id')
        member.pop('created_at')
        member.pop('updated_at')
        self.assertEqual(expected, member)

    def test_image_member_update_status(self):
        TENANT1 = str(uuid.uuid4())
        # NOTE(flaper87): Update auth token, otherwise
        # non visible members won't be returned.
        self.context.auth_token = 'user:%s:user' % TENANT1
        member = self.db_api.image_member_create(self.context,
                                                 {'member': TENANT1,
                                                  'image_id': UUID1})
        member_id = member.pop('id')
        member.pop('created_at')
        member.pop('updated_at')

        expected = {'member': TENANT1,
                    'image_id': UUID1,
                    'status': 'pending',
                    'can_share': False,
                    'deleted': False}
        self.assertEqual(expected, member)

        member = self.db_api.image_member_update(self.context,
                                                 member_id,
                                                 {'status': 'accepted'})

        self.assertNotEqual(member['created_at'], member['updated_at'])
        member.pop('id')
        member.pop('created_at')
        member.pop('updated_at')
        expected = {'member': TENANT1,
                    'image_id': UUID1,
                    'status': 'accepted',
                    'can_share': False,
                    'deleted': False}
        self.assertEqual(expected, member)

        members = self.db_api.image_member_find(self.context,
                                                member=TENANT1,
                                                image_id=UUID1)
        member = members[0]
        member.pop('id')
        member.pop('created_at')
        member.pop('updated_at')
        self.assertEqual(expected, member)

    def test_image_member_find(self):
        TENANT1 = str(uuid.uuid4())
        TENANT2 = str(uuid.uuid4())
        fixtures = [
            {'member': TENANT1, 'image_id': UUID1},
            {'member': TENANT1, 'image_id': UUID2, 'status': 'rejected'},
            {'member': TENANT2, 'image_id': UUID1, 'status': 'accepted'},
        ]
        for f in fixtures:
            self.db_api.image_member_create(self.context, copy.deepcopy(f))

        def _simplify(output):
            return

        def _assertMemberListMatch(list1, list2):
            def _simple(x):
                return set([(o['member'], o['image_id']) for o in x])

            self.assertEqual(_simple(list1), _simple(list2))

        # NOTE(flaper87): Update auth token, otherwise
        # non visible members won't be returned.
        self.context.auth_token = 'user:%s:user' % TENANT1
        output = self.db_api.image_member_find(self.context, member=TENANT1)
        _assertMemberListMatch([fixtures[0], fixtures[1]], output)

        output = self.db_api.image_member_find(self.adm_context,
                                               image_id=UUID1)
        _assertMemberListMatch([fixtures[0], fixtures[2]], output)

        # NOTE(flaper87): Update auth token, otherwise
        # non visible members won't be returned.
        self.context.auth_token = 'user:%s:user' % TENANT2
        output = self.db_api.image_member_find(self.context,
                                               member=TENANT2,
                                               image_id=UUID1)
        _assertMemberListMatch([fixtures[2]], output)

        output = self.db_api.image_member_find(self.context,
                                               status='accepted')
        _assertMemberListMatch([fixtures[2]], output)

        # NOTE(flaper87): Update auth token, otherwise
        # non visible members won't be returned.
        self.context.auth_token = 'user:%s:user' % TENANT1
        output = self.db_api.image_member_find(self.context,
                                               status='rejected')
        _assertMemberListMatch([fixtures[1]], output)

        output = self.db_api.image_member_find(self.context,
                                               status='pending')
        _assertMemberListMatch([fixtures[0]], output)

        output = self.db_api.image_member_find(self.context,
                                               status='pending',
                                               image_id=UUID2)
        _assertMemberListMatch([], output)

        image_id = str(uuid.uuid4())
        output = self.db_api.image_member_find(self.context,
                                               member=TENANT2,
                                               image_id=image_id)
        _assertMemberListMatch([], output)

    def test_image_member_count(self):
        TENANT1 = str(uuid.uuid4())
        self.db_api.image_member_create(self.context,
                                        {'member': TENANT1,
                                         'image_id': UUID1})

        actual = self.db_api.image_member_count(self.context, UUID1)

        self.assertEqual(1, actual)

    def test_image_member_count_invalid_image_id(self):
        TENANT1 = str(uuid.uuid4())
        self.db_api.image_member_create(self.context,
                                        {'member': TENANT1,
                                         'image_id': UUID1})

        self.assertRaises(exception.Invalid, self.db_api.image_member_count,
                          self.context, None)

    def test_image_member_count_empty_image_id(self):
        TENANT1 = str(uuid.uuid4())
        self.db_api.image_member_create(self.context,
                                        {'member': TENANT1,
                                         'image_id': UUID1})

        self.assertRaises(exception.Invalid, self.db_api.image_member_count,
                          self.context, "")

    def test_image_member_delete(self):
        TENANT1 = str(uuid.uuid4())
        # NOTE(flaper87): Update auth token, otherwise
        # non visible members won't be returned.
        self.context.auth_token = 'user:%s:user' % TENANT1
        fixture = {'member': TENANT1, 'image_id': UUID1, 'can_share': True}
        member = self.db_api.image_member_create(self.context, fixture)
        self.assertEqual(1, len(self.db_api.image_member_find(self.context)))
        member = self.db_api.image_member_delete(self.context, member['id'])
        self.assertEqual(0, len(self.db_api.image_member_find(self.context)))


class DriverQuotaTests(test_utils.BaseTestCase):

    def setUp(self):
        super(DriverQuotaTests, self).setUp()
        self.owner_id1 = str(uuid.uuid4())
        self.context1 = context.RequestContext(
            is_admin=False, user=self.owner_id1, tenant=self.owner_id1,
            auth_token='%s:%s:user' % (self.owner_id1, self.owner_id1))
        self.db_api = db_tests.get_db(self.config)
        db_tests.reset_db(self.db_api)
        dt1 = oslo_timeutils.utcnow()
        dt2 = dt1 + datetime.timedelta(microseconds=5)
        fixtures = [
            {
                'id': UUID1,
                'created_at': dt1,
                'updated_at': dt1,
                'size': 13,
                'owner': self.owner_id1,
            },
            {
                'id': UUID2,
                'created_at': dt1,
                'updated_at': dt2,
                'size': 17,
                'owner': self.owner_id1,
            },
            {
                'id': UUID3,
                'created_at': dt2,
                'updated_at': dt2,
                'size': 7,
                'owner': self.owner_id1,
            },
        ]
        self.owner1_fixtures = [
            build_image_fixture(**fixture) for fixture in fixtures]

        for fixture in self.owner1_fixtures:
            self.db_api.image_create(self.context1, fixture)

    def test_storage_quota(self):
        total = functools.reduce(
            lambda x, y: x + y,
            [f['size'] for f in self.owner1_fixtures],
        )
        x = self.db_api.user_get_storage_usage(self.context1, self.owner_id1)
        self.assertEqual(total, x)

    def test_storage_quota_without_image_id(self):
        total = functools.reduce(
            lambda x, y: x + y,
            [f['size'] for f in self.owner1_fixtures],
        )
        total = total - self.owner1_fixtures[0]['size']
        x = self.db_api.user_get_storage_usage(
            self.context1, self.owner_id1,
            image_id=self.owner1_fixtures[0]['id'])
        self.assertEqual(total, x)

    def test_storage_quota_multiple_locations(self):
        dt1 = oslo_timeutils.utcnow()
        sz = 53
        new_fixture_dict = {'id': str(uuid.uuid4()), 'created_at': dt1,
                            'updated_at': dt1, 'size': sz,
                            'owner': self.owner_id1}
        new_fixture = build_image_fixture(**new_fixture_dict)
        new_fixture['locations'].append({'url': 'file:///some/path/file',
                                         'metadata': {},
                                         'status': 'active'})
        self.db_api.image_create(self.context1, new_fixture)

        total = functools.reduce(
            lambda x, y: x + y,
            [f['size'] for f in self.owner1_fixtures],
        ) + (sz * 2)
        x = self.db_api.user_get_storage_usage(self.context1, self.owner_id1)
        self.assertEqual(total, x)

    def test_storage_quota_deleted_image(self):
        # NOTE(flaper87): This needs to be tested for
        # soft deleted images as well. Currently there's no
        # good way to delete locations.
        dt1 = oslo_timeutils.utcnow()
        sz = 53
        image_id = str(uuid.uuid4())
        new_fixture_dict = {'id': image_id, 'created_at': dt1,
                            'updated_at': dt1, 'size': sz,
                            'owner': self.owner_id1}
        new_fixture = build_image_fixture(**new_fixture_dict)
        new_fixture['locations'].append({'url': 'file:///some/path/file',
                                         'metadata': {},
                                         'status': 'active'})
        self.db_api.image_create(self.context1, new_fixture)

        total = functools.reduce(
            lambda x, y: x + y,
            [f['size'] for f in self.owner1_fixtures],
        )
        x = self.db_api.user_get_storage_usage(self.context1, self.owner_id1)
        self.assertEqual(total + (sz * 2), x)

        self.db_api.image_destroy(self.context1, image_id)
        x = self.db_api.user_get_storage_usage(self.context1, self.owner_id1)
        self.assertEqual(total, x)


class TaskTests(test_utils.BaseTestCase):

    def setUp(self):
        super(TaskTests, self).setUp()
        self.admin_id = 'admin'
        self.owner_id = 'user'
        self.adm_context = context.RequestContext(
            is_admin=True, auth_token='user:admin:admin', tenant=self.admin_id)
        self.context = context.RequestContext(
            is_admin=False, auth_token='user:user:user', user=self.owner_id)
        self.db_api = db_tests.get_db(self.config)
        self.fixtures = self.build_task_fixtures()
        db_tests.reset_db(self.db_api)

    def build_task_fixtures(self):
        self.context.project_id = str(uuid.uuid4())
        fixtures = [
            {
                'owner': self.context.owner,
                'type': 'import',
                'input': {'import_from': 'file:///a.img',
                          'import_from_format': 'qcow2',
                          'image_properties': {
                              "name": "GreatStack 1.22",
                              "tags": ["lamp", "custom"]
                          }},
            },
            {
                'owner': self.context.owner,
                'type': 'import',
                'input': {'import_from': 'file:///b.img',
                          'import_from_format': 'qcow2',
                          'image_properties': {
                              "name": "GreatStack 1.23",
                              "tags": ["lamp", "good"]
                          }},
            },
            {
                'owner': self.context.owner,
                "type": "export",
                "input": {
                    "export_uuid": "deadbeef-dead-dead-dead-beefbeefbeef",
                    "export_to":
                        "swift://cloud.foo/myaccount/mycontainer/path",
                    "export_format": "qcow2"
                }
            },
        ]
        return [build_task_fixture(**fixture) for fixture in fixtures]

    def test_task_get_all_with_filter(self):
        for fixture in self.fixtures:
            self.db_api.task_create(self.adm_context,
                                    build_task_fixture(**fixture))

        import_tasks = self.db_api.task_get_all(self.adm_context,
                                                filters={'type': 'import'})

        self.assertTrue(import_tasks)
        self.assertEqual(2, len(import_tasks))
        for task in import_tasks:
            self.assertEqual('import', task['type'])
            self.assertEqual(self.context.owner, task['owner'])

    def test_task_get_all_as_admin(self):
        tasks = []
        for fixture in self.fixtures:
            task = self.db_api.task_create(self.adm_context,
                                           build_task_fixture(**fixture))
            tasks.append(task)
        import_tasks = self.db_api.task_get_all(self.adm_context)
        self.assertTrue(import_tasks)
        self.assertEqual(3, len(import_tasks))

    def test_task_get_all_marker(self):
        for fixture in self.fixtures:
            self.db_api.task_create(self.adm_context,
                                    build_task_fixture(**fixture))
        tasks = self.db_api.task_get_all(self.adm_context, sort_key='id')
        task_ids = [t['id'] for t in tasks]
        tasks = self.db_api.task_get_all(self.adm_context, sort_key='id',
                                         marker=task_ids[0])
        self.assertEqual(2, len(tasks))

    def test_task_get_all_limit(self):
        for fixture in self.fixtures:
            self.db_api.task_create(self.adm_context,
                                    build_task_fixture(**fixture))

        tasks = self.db_api.task_get_all(self.adm_context, limit=2)
        self.assertEqual(2, len(tasks))

        # A limit of None should not equate to zero
        tasks = self.db_api.task_get_all(self.adm_context, limit=None)
        self.assertEqual(3, len(tasks))

        # A limit of zero should actually mean zero
        tasks = self.db_api.task_get_all(self.adm_context, limit=0)
        self.assertEqual(0, len(tasks))

    def test_task_get_all_owned(self):
        then = oslo_timeutils.utcnow() + datetime.timedelta(days=365)
        TENANT1 = str(uuid.uuid4())
        ctxt1 = context.RequestContext(is_admin=False,
                                       tenant=TENANT1,
                                       auth_token='user:%s:user' % TENANT1)

        task_values = {'type': 'import', 'status': 'pending',
                       'input': '{"loc": "fake"}', 'owner': TENANT1,
                       'expires_at': then}
        self.db_api.task_create(ctxt1, task_values)

        TENANT2 = str(uuid.uuid4())
        ctxt2 = context.RequestContext(is_admin=False,
                                       tenant=TENANT2,
                                       auth_token='user:%s:user' % TENANT2)

        task_values = {'type': 'export', 'status': 'pending',
                       'input': '{"loc": "fake"}', 'owner': TENANT2,
                       'expires_at': then}
        self.db_api.task_create(ctxt2, task_values)

        tasks = self.db_api.task_get_all(ctxt1)

        task_owners = set([task['owner'] for task in tasks])
        expected = set([TENANT1])
        self.assertEqual(sorted(expected), sorted(task_owners))

    def test_task_get(self):
        expires_at = oslo_timeutils.utcnow()
        image_id = str(uuid.uuid4())
        fixture = {
            'owner': self.context.owner,
            'type': 'import',
            'status': 'pending',
            'input': '{"loc": "fake"}',
            'result': "{'image_id': %s}" % image_id,
            'message': 'blah',
            'expires_at': expires_at
        }

        task = self.db_api.task_create(self.adm_context, fixture)

        self.assertIsNotNone(task)
        self.assertIsNotNone(task['id'])

        task_id = task['id']
        task = self.db_api.task_get(self.adm_context, task_id)

        self.assertIsNotNone(task)
        self.assertEqual(task_id, task['id'])
        self.assertEqual(self.context.owner, task['owner'])
        self.assertEqual('import', task['type'])
        self.assertEqual('pending', task['status'])
        self.assertEqual(fixture['input'], task['input'])
        self.assertEqual(fixture['result'], task['result'])
        self.assertEqual(fixture['message'], task['message'])
        self.assertEqual(expires_at, task['expires_at'])

    def _test_task_get_by_image(self, expired=False, deleted=False,
                                other_owner=False):
        expires_at = oslo_timeutils.utcnow()
        if expired is False:
            expires_at += datetime.timedelta(hours=1)
        elif expired is None:
            # This is the case where we haven't even processed the task
            # to give it an expiry time.
            expires_at = None
        image_id = str(uuid.uuid4())
        fixture = {
            'owner': other_owner and 'notme!' or self.context.owner,
            'type': 'import',
            'status': 'pending',
            'input': '{"loc": "fake"}',
            'result': "{'image_id': %s}" % image_id,
            'message': 'blah',
            'expires_at': expires_at,
            'image_id': image_id,
            'user_id': 'me',
            'request_id': 'reqid',
        }

        new_task = self.db_api.task_create(self.adm_context, fixture)
        if deleted:
            self.db_api.task_delete(self.context, new_task['id'])
        return (new_task['id'],
                self.db_api.tasks_get_by_image(self.context, image_id))

    def test_task_get_by_image_not_expired(self):
        # Make sure we get back the task
        task_id, tasks = self._test_task_get_by_image(expired=False)
        self.assertEqual(1, len(tasks))
        self.assertEqual(task_id, tasks[0]['id'])

    def test_task_get_by_image_expired(self):
        # Make sure we do not retrieve the expired task
        task_id, tasks = self._test_task_get_by_image(expired=True)
        self.assertEqual(0, len(tasks))

        # We should have deleted the task while querying for it, so make
        # sure that our task is now marked as deleted.
        tasks = self.db_api.task_get_all(self.adm_context)
        self.assertEqual(1, len(tasks))
        self.assertEqual(task_id, tasks[0]['id'])
        self.assertTrue(tasks[0]['deleted'])

    def test_task_get_by_image_no_expiry(self):
        # Make sure we find the task that has expires_at=NULL
        task_id, tasks = self._test_task_get_by_image(expired=None)
        self.assertEqual(1, len(tasks))

        # The task should have been retrieved above, and it's also not
        # deleted because it doesn't have an expiry, so it should
        # still be in the DB.
        tasks = self.db_api.task_get_all(self.adm_context)
        self.assertEqual(1, len(tasks))
        self.assertEqual(task_id, tasks[0]['id'])
        self.assertFalse(tasks[0]['deleted'])
        self.assertIsNone(tasks[0]['expires_at'])

    def test_task_get_by_image_deleted(self):
        task_id, tasks = self._test_task_get_by_image(deleted=True)
        # We cannot see the deleted tasks
        self.assertEqual(0, len(tasks))

    def test_task_get_by_image_not_mine(self):
        task_id, tasks = self._test_task_get_by_image(other_owner=True)
        # We cannot see tasks we do not own
        self.assertEqual(0, len(tasks))

    def test_task_get_all(self):
        now = oslo_timeutils.utcnow()
        then = now + datetime.timedelta(days=365)
        image_id = str(uuid.uuid4())
        fixture1 = {
            'owner': self.context.owner,
            'type': 'import',
            'status': 'pending',
            'input': '{"loc": "fake_1"}',
            'result': "{'image_id': %s}" % image_id,
            'message': 'blah_1',
            'expires_at': then,
            'created_at': now,
            'updated_at': now
        }

        fixture2 = {
            'owner': self.context.owner,
            'type': 'import',
            'status': 'pending',
            'input': '{"loc": "fake_2"}',
            'result': "{'image_id': %s}" % image_id,
            'message': 'blah_2',
            'expires_at': then,
            'created_at': now,
            'updated_at': now
        }

        task1 = self.db_api.task_create(self.adm_context, fixture1)
        task2 = self.db_api.task_create(self.adm_context, fixture2)

        self.assertIsNotNone(task1)
        self.assertIsNotNone(task2)

        task1_id = task1['id']
        task2_id = task2['id']
        task_fixtures = {task1_id: fixture1, task2_id: fixture2}
        tasks = self.db_api.task_get_all(self.adm_context)

        self.assertEqual(2, len(tasks))
        self.assertEqual(set((tasks[0]['id'], tasks[1]['id'])),
                         set((task1_id, task2_id)))
        for task in tasks:
            fixture = task_fixtures[task['id']]

            self.assertEqual(self.context.owner, task['owner'])
            self.assertEqual(fixture['type'], task['type'])
            self.assertEqual(fixture['status'], task['status'])
            self.assertEqual(fixture['expires_at'], task['expires_at'])
            self.assertFalse(task['deleted'])
            self.assertIsNone(task['deleted_at'])
            self.assertEqual(fixture['created_at'], task['created_at'])
            self.assertEqual(fixture['updated_at'], task['updated_at'])
            task_details_keys = ['input', 'message', 'result']
            for key in task_details_keys:
                self.assertNotIn(key, task)

    def test_task_soft_delete(self):
        now = oslo_timeutils.utcnow()
        then = now + datetime.timedelta(days=365)

        fixture1 = build_task_fixture(id='1', expires_at=now,
                                      owner=self.adm_context.owner)
        fixture2 = build_task_fixture(id='2', expires_at=now,
                                      owner=self.adm_context.owner)
        fixture3 = build_task_fixture(id='3', expires_at=then,
                                      owner=self.adm_context.owner)
        fixture4 = build_task_fixture(id='4', expires_at=then,
                                      owner=self.adm_context.owner)

        task1 = self.db_api.task_create(self.adm_context, fixture1)
        task2 = self.db_api.task_create(self.adm_context, fixture2)
        task3 = self.db_api.task_create(self.adm_context, fixture3)
        task4 = self.db_api.task_create(self.adm_context, fixture4)

        self.assertIsNotNone(task1)
        self.assertIsNotNone(task2)
        self.assertIsNotNone(task3)
        self.assertIsNotNone(task4)

        tasks = self.db_api.task_get_all(
            self.adm_context, sort_key='id', sort_dir='asc')

        self.assertEqual(4, len(tasks))

        self.assertTrue(tasks[0]['deleted'])
        self.assertTrue(tasks[1]['deleted'])
        self.assertFalse(tasks[2]['deleted'])
        self.assertFalse(tasks[3]['deleted'])

    def test_task_create(self):
        task_id = str(uuid.uuid4())
        self.context.project_id = self.context.owner
        values = {
            'id': task_id,
            'owner': self.context.owner,
            'type': 'export',
            'status': 'pending',
        }
        task_values = build_task_fixture(**values)
        task = self.db_api.task_create(self.adm_context, task_values)
        self.assertIsNotNone(task)
        self.assertEqual(task_id, task['id'])
        self.assertEqual(self.context.owner, task['owner'])
        self.assertEqual('export', task['type'])
        self.assertEqual('pending', task['status'])
        self.assertEqual({'ping': 'pong'}, task['input'])

    def test_task_create_with_all_task_info_null(self):
        task_id = str(uuid.uuid4())
        self.context.project_id = str(uuid.uuid4())
        values = {
            'id': task_id,
            'owner': self.context.owner,
            'type': 'export',
            'status': 'pending',
            'input': None,
            'result': None,
            'message': None,
        }
        task_values = build_task_fixture(**values)
        task = self.db_api.task_create(self.adm_context, task_values)
        self.assertIsNotNone(task)
        self.assertEqual(task_id, task['id'])
        self.assertEqual(self.context.owner, task['owner'])
        self.assertEqual('export', task['type'])
        self.assertEqual('pending', task['status'])
        self.assertIsNone(task['input'])
        self.assertIsNone(task['result'])
        self.assertIsNone(task['message'])

    def test_task_update(self):
        self.context.project_id = str(uuid.uuid4())
        result = {'foo': 'bar'}
        task_values = build_task_fixture(owner=self.context.owner,
                                         result=result)
        task = self.db_api.task_create(self.adm_context, task_values)

        task_id = task['id']
        fixture = {
            'status': 'processing',
            'message': 'This is a error string',
        }
        task = self.db_api.task_update(self.adm_context, task_id, fixture)

        self.assertEqual(task_id, task['id'])
        self.assertEqual(self.context.owner, task['owner'])
        self.assertEqual('import', task['type'])
        self.assertEqual('processing', task['status'])
        self.assertEqual({'ping': 'pong'}, task['input'])
        self.assertEqual(result, task['result'])
        self.assertEqual('This is a error string', task['message'])
        self.assertFalse(task['deleted'])
        self.assertIsNone(task['deleted_at'])
        self.assertIsNone(task['expires_at'])
        self.assertEqual(task_values['created_at'], task['created_at'])
        self.assertGreater(task['updated_at'], task['created_at'])

    def test_task_update_with_all_task_info_null(self):
        self.context.project_id = str(uuid.uuid4())
        task_values = build_task_fixture(owner=self.context.owner,
                                         input=None,
                                         result=None,
                                         message=None)
        task = self.db_api.task_create(self.adm_context, task_values)

        task_id = task['id']
        fixture = {'status': 'processing'}
        task = self.db_api.task_update(self.adm_context, task_id, fixture)

        self.assertEqual(task_id, task['id'])
        self.assertEqual(self.context.owner, task['owner'])
        self.assertEqual('import', task['type'])
        self.assertEqual('processing', task['status'])
        self.assertIsNone(task['input'])
        self.assertIsNone(task['result'])
        self.assertIsNone(task['message'])
        self.assertFalse(task['deleted'])
        self.assertIsNone(task['deleted_at'])
        self.assertIsNone(task['expires_at'])
        self.assertEqual(task_values['created_at'], task['created_at'])
        self.assertGreater(task['updated_at'], task['created_at'])

    def test_task_delete(self):
        task_values = build_task_fixture(owner=self.context.owner)
        task = self.db_api.task_create(self.adm_context, task_values)

        self.assertIsNotNone(task)
        self.assertFalse(task['deleted'])
        self.assertIsNone(task['deleted_at'])

        task_id = task['id']
        self.db_api.task_delete(self.adm_context, task_id)
        self.assertRaises(exception.TaskNotFound, self.db_api.task_get,
                          self.context, task_id)

    def test_task_delete_as_admin(self):
        task_values = build_task_fixture(owner=self.context.owner)
        task = self.db_api.task_create(self.adm_context, task_values)

        self.assertIsNotNone(task)
        self.assertFalse(task['deleted'])
        self.assertIsNone(task['deleted_at'])

        task_id = task['id']
        self.db_api.task_delete(self.adm_context, task_id)
        del_task = self.db_api.task_get(self.adm_context,
                                        task_id,
                                        force_show_deleted=True)
        self.assertIsNotNone(del_task)
        self.assertEqual(task_id, del_task['id'])
        self.assertTrue(del_task['deleted'])
        self.assertIsNotNone(del_task['deleted_at'])


class DBPurgeTests(test_utils.BaseTestCase):

    def setUp(self):
        super(DBPurgeTests, self).setUp()
        self.adm_context = context.get_admin_context(show_deleted=True)
        self.db_api = db_tests.get_db(self.config)
        db_tests.reset_db(self.db_api)
        self.context = context.RequestContext(is_admin=True)
        self.image_fixtures, self.task_fixtures = self.build_fixtures()
        self.create_tasks(self.task_fixtures)
        self.create_images(self.image_fixtures)

    def build_fixtures(self):
        dt1 = oslo_timeutils.utcnow() - datetime.timedelta(days=5)
        dt2 = dt1 + datetime.timedelta(days=1)
        dt3 = dt2 + datetime.timedelta(days=1)
        fixtures = [
            {
                'created_at': dt1,
                'updated_at': dt1,
                'deleted_at': dt3,
                'deleted': True,
            },
            {
                'created_at': dt1,
                'updated_at': dt2,
                'deleted_at': oslo_timeutils.utcnow(),
                'deleted': True,
            },
            {
                'created_at': dt2,
                'updated_at': dt2,
                'deleted_at': None,
                'deleted': False,
            },
        ]
        return (
            [build_image_fixture(**fixture) for fixture in fixtures],
            [build_task_fixture(**fixture) for fixture in fixtures],
        )

    def create_images(self, images):
        for fixture in images:
            self.db_api.image_create(self.adm_context, fixture)

    def create_tasks(self, tasks):
        for fixture in tasks:
            self.db_api.task_create(self.adm_context, fixture)

    def test_db_purge(self):
        self.db_api.purge_deleted_rows(self.adm_context, 1, 5)
        images = self.db_api.image_get_all(self.adm_context)

        # Verify that no records from images have been deleted
        # as images table will be purged using 'purge_images_table'
        # command.
        self.assertEqual(len(images), 3)
        tasks = self.db_api.task_get_all(self.adm_context)
        self.assertEqual(len(tasks), 2)

    def test_db_purge_images_table(self):
        images = self.db_api.image_get_all(self.adm_context)
        self.assertEqual(len(images), 3)
        tasks = self.db_api.task_get_all(self.adm_context)
        self.assertEqual(len(tasks), 3)

        # purge records from locations table
        for image in images:
            session = self.db_api.get_session()
            with session.begin():
                session.execute(
                    sql.delete(models.ImageLocation)
                    .where(models.ImageLocation.image_id == image['id'])
                )

        # purge records from images_tags table
        self.db_api.purge_deleted_rows(self.adm_context, 1, 5)

        # purge records from images table
        self.db_api.purge_deleted_rows_from_images(self.adm_context, 1, 5)

        images = self.db_api.image_get_all(self.adm_context)
        self.assertEqual(len(images), 2)
        tasks = self.db_api.task_get_all(self.adm_context)
        self.assertEqual(len(tasks), 2)

    def test_purge_images_table_fk_constraint_failure(self):
        """Test foreign key constraint failure

        Test whether foreign key constraint failure during purge
        operation is raising DBReferenceError or not.
        """
        session = db_api.get_session()
        engine = db_api.get_engine()
        connection = engine.connect()

        images = sqlalchemyutils.get_table(engine, "images")
        image_tags = sqlalchemyutils.get_table(engine, "image_tags")

        # Add a 4th row in images table and set it deleted 15 days ago
        uuidstr = uuid.uuid4().hex
        created_time = oslo_timeutils.utcnow() - datetime.timedelta(days=20)
        deleted_time = created_time + datetime.timedelta(days=5)
        images_row_fixture = {
            'id': uuidstr,
            'status': 'status',
            'created_at': created_time,
            'deleted_at': deleted_time,
            'deleted': 1,
            'visibility': 'public',
            'min_disk': 1,
            'min_ram': 1,
            'protected': 0
        }
        ins_stmt = images.insert().values(**images_row_fixture)
        with connection.begin():
            connection.execute(ins_stmt)

        # Add a record in image_tags referencing the above images record
        # but do not set it as deleted
        image_tags_row_fixture = {
            'image_id': uuidstr,
            'value': 'tag_value',
            'created_at': created_time,
            'deleted': 0
        }
        ins_stmt = image_tags.insert().values(**image_tags_row_fixture)
        with connection.begin():
            connection.execute(ins_stmt)

        # Purge all records deleted at least 10 days ago
        self.assertRaises(db_exception.DBReferenceError,
                          db_api.purge_deleted_rows_from_images,
                          self.adm_context,
                          age_in_days=10,
                          max_rows=50)

        # Verify that no records from images have been deleted
        # due to DBReferenceError being raised
        with session.begin():
            images_rows = session.query(images).count()
        self.assertEqual(4, images_rows)

    def test_purge_task_info_with_refs_to_soft_deleted_tasks(self):
        session = db_api.get_session()
        engine = db_api.get_engine()

        # check initial task and task_info row number are 3
        tasks = self.db_api.task_get_all(self.adm_context)
        self.assertEqual(3, len(tasks))

        task_info = sqlalchemyutils.get_table(engine, 'task_info')
        with session.begin():
            task_info_rows = session.query(task_info).count()
        self.assertEqual(3, task_info_rows)

        # purge soft deleted rows older than yesterday
        self.db_api.purge_deleted_rows(self.context, 1, 5)

        # check 1 row of task table is purged
        tasks = self.db_api.task_get_all(self.adm_context)
        self.assertEqual(2, len(tasks))

        # and no task_info was left behind, 1 row purged
        with session.begin():
            task_info_rows = session.query(task_info).count()
        self.assertEqual(2, task_info_rows)


class TestVisibility(test_utils.BaseTestCase):
    def setUp(self):
        super(TestVisibility, self).setUp()
        self.db_api = db_tests.get_db(self.config)
        db_tests.reset_db(self.db_api)
        self.setup_tenants()
        self.setup_contexts()
        self.fixtures = self.build_image_fixtures()
        self.create_images(self.fixtures)

    def setup_tenants(self):
        self.admin_tenant = str(uuid.uuid4())
        self.tenant1 = str(uuid.uuid4())
        self.tenant2 = str(uuid.uuid4())

    def setup_contexts(self):
        self.admin_context = context.RequestContext(
            is_admin=True, tenant=self.admin_tenant)
        self.admin_none_context = context.RequestContext(
            is_admin=True, tenant=None)
        self.tenant1_context = context.RequestContext(tenant=self.tenant1)
        self.tenant2_context = context.RequestContext(tenant=self.tenant2)
        self.none_context = context.RequestContext(tenant=None)

    def build_image_fixtures(self):
        fixtures = []
        owners = {
            'Unowned': None,
            'Admin Tenant': self.admin_tenant,
            'Tenant 1': self.tenant1,
            'Tenant 2': self.tenant2,
        }
        visibilities = ['community', 'private', 'public', 'shared']
        for owner_label, owner in owners.items():
            for visibility in visibilities:
                fixture = {
                    'name': '%s, %s' % (owner_label, visibility),
                    'owner': owner,
                    'visibility': visibility,
                }
                fixtures.append(fixture)
        return [build_image_fixture(**f) for f in fixtures]

    def create_images(self, images):
        for fixture in images:
            self.db_api.image_create(self.admin_context, fixture)


class VisibilityTests(object):

    def test_unknown_admin_sees_all_but_community(self):
        images = self.db_api.image_get_all(self.admin_none_context)
        self.assertEqual(12, len(images))

    def test_unknown_admin_is_public_true(self):
        images = self.db_api.image_get_all(self.admin_none_context,
                                           is_public=True)
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_unknown_admin_is_public_false(self):
        images = self.db_api.image_get_all(self.admin_none_context,
                                           is_public=False)
        self.assertEqual(8, len(images))
        for i in images:
            self.assertIn(i['visibility'], ['shared', 'private'])

    def test_unknown_admin_is_public_none(self):
        images = self.db_api.image_get_all(self.admin_none_context)
        self.assertEqual(12, len(images))

    def test_unknown_admin_visibility_public(self):
        images = self.db_api.image_get_all(self.admin_none_context,
                                           filters={'visibility': 'public'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_unknown_admin_visibility_shared(self):
        images = self.db_api.image_get_all(self.admin_none_context,
                                           filters={'visibility': 'shared'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('shared', i['visibility'])

    def test_unknown_admin_visibility_private(self):
        images = self.db_api.image_get_all(self.admin_none_context,
                                           filters={'visibility': 'private'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('private', i['visibility'])

    def test_unknown_admin_visibility_community(self):
        images = self.db_api.image_get_all(self.admin_none_context,
                                           filters={'visibility': 'community'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('community', i['visibility'])

    def test_unknown_admin_visibility_all(self):
        images = self.db_api.image_get_all(self.admin_none_context,
                                           filters={'visibility': 'all'})
        self.assertEqual(16, len(images))

    def test_known_admin_sees_all_but_others_community_images(self):
        images = self.db_api.image_get_all(self.admin_context)
        self.assertEqual(13, len(images))

    def test_known_admin_is_public_true(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=True)
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_known_admin_is_public_false(self):
        images = self.db_api.image_get_all(self.admin_context,
                                           is_public=False)
        self.assertEqual(9, len(images))
        for i in images:
            self.assertIn(i['visibility'], ['shared', 'private', 'community'])

    def test_known_admin_is_public_none(self):
        images = self.db_api.image_get_all(self.admin_context)
        self.assertEqual(13, len(images))

    def test_admin_as_user_true(self):
        images = self.db_api.image_get_all(self.admin_context,
                                           admin_as_user=True)
        self.assertEqual(7, len(images))
        for i in images:
            self.assertTrue(('public' == i['visibility'])
                            or i['owner'] == self.admin_tenant)

    def test_known_admin_visibility_public(self):
        images = self.db_api.image_get_all(self.admin_context,
                                           filters={'visibility': 'public'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_known_admin_visibility_shared(self):
        images = self.db_api.image_get_all(self.admin_context,
                                           filters={'visibility': 'shared'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('shared', i['visibility'])

    def test_known_admin_visibility_private(self):
        images = self.db_api.image_get_all(self.admin_context,
                                           filters={'visibility': 'private'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('private', i['visibility'])

    def test_known_admin_visibility_community(self):
        images = self.db_api.image_get_all(self.admin_context,
                                           filters={'visibility': 'community'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('community', i['visibility'])

    def test_known_admin_visibility_all(self):
        images = self.db_api.image_get_all(self.admin_context,
                                           filters={'visibility': 'all'})
        self.assertEqual(16, len(images))

    def test_what_unknown_user_sees(self):
        images = self.db_api.image_get_all(self.none_context)
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_unknown_user_is_public_true(self):
        images = self.db_api.image_get_all(self.none_context, is_public=True)
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_unknown_user_is_public_false(self):
        images = self.db_api.image_get_all(self.none_context, is_public=False)
        self.assertEqual(0, len(images))

    def test_unknown_user_is_public_none(self):
        images = self.db_api.image_get_all(self.none_context)
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_unknown_user_visibility_public(self):
        images = self.db_api.image_get_all(self.none_context,
                                           filters={'visibility': 'public'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_unknown_user_visibility_shared(self):
        images = self.db_api.image_get_all(self.none_context,
                                           filters={'visibility': 'shared'})
        self.assertEqual(0, len(images))

    def test_unknown_user_visibility_private(self):
        images = self.db_api.image_get_all(self.none_context,
                                           filters={'visibility': 'private'})
        self.assertEqual(0, len(images))

    def test_unknown_user_visibility_community(self):
        images = self.db_api.image_get_all(self.none_context,
                                           filters={'visibility': 'community'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('community', i['visibility'])

    def test_unknown_user_visibility_all(self):
        images = self.db_api.image_get_all(self.none_context,
                                           filters={'visibility': 'all'})
        self.assertEqual(8, len(images))

    def test_what_tenant1_sees(self):
        images = self.db_api.image_get_all(self.tenant1_context)
        self.assertEqual(7, len(images))
        for i in images:
            if not ('public' == i['visibility']):
                self.assertEqual(i['owner'], self.tenant1)

    def test_tenant1_is_public_true(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=True)
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_tenant1_is_public_false(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=False)
        self.assertEqual(3, len(images))
        for i in images:
            self.assertEqual(i['owner'], self.tenant1)
            self.assertIn(i['visibility'], ['private', 'shared', 'community'])

    def test_tenant1_is_public_none(self):
        images = self.db_api.image_get_all(self.tenant1_context)
        self.assertEqual(7, len(images))
        for i in images:
            if not ('public' == i['visibility']):
                self.assertEqual(self.tenant1, i['owner'])

    def test_tenant1_visibility_public(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           filters={'visibility': 'public'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('public', i['visibility'])

    def test_tenant1_visibility_shared(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           filters={'visibility': 'shared'})
        self.assertEqual(1, len(images))
        self.assertEqual('shared', images[0]['visibility'])
        self.assertEqual(self.tenant1, images[0]['owner'])

    def test_tenant1_visibility_private(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           filters={'visibility': 'private'})
        self.assertEqual(1, len(images))
        self.assertEqual('private', images[0]['visibility'])
        self.assertEqual(self.tenant1, images[0]['owner'])

    def test_tenant1_visibility_community(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           filters={'visibility': 'community'})
        self.assertEqual(4, len(images))
        for i in images:
            self.assertEqual('community', i['visibility'])

    def test_tenant1_visibility_all(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           filters={'visibility': 'all'})
        self.assertEqual(10, len(images))

    def _setup_is_public_red_herring(self):
        values = {
            'name': 'Red Herring',
            'owner': self.tenant1,
            'visibility': 'shared',
            'properties': {'is_public': 'silly'}
        }
        fixture = build_image_fixture(**values)
        self.db_api.image_create(self.admin_context, fixture)

    def test_is_public_is_a_normal_filter_for_admin(self):
        self._setup_is_public_red_herring()
        images = self.db_api.image_get_all(self.admin_context,
                                           filters={'is_public': 'silly'})
        self.assertEqual(1, len(images))
        self.assertEqual('Red Herring', images[0]['name'])

    def test_is_public_is_a_normal_filter_for_user(self):
        self._setup_is_public_red_herring()
        images = self.db_api.image_get_all(self.tenant1_context,
                                           filters={'is_public': 'silly'})
        self.assertEqual(1, len(images))
        self.assertEqual('Red Herring', images[0]['name'])

    # NOTE(markwash): the following tests are sanity checks to make sure
    # visibility filtering and is_public=(True|False) do not interact in
    # unexpected ways. However, using both of the filtering techniques
    # simultaneously is not an anticipated use case.

    def test_admin_is_public_true_and_visibility_public(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=True,
                                           filters={'visibility': 'public'})
        self.assertEqual(4, len(images))

    def test_admin_is_public_false_and_visibility_public(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=False,
                                           filters={'visibility': 'public'})
        self.assertEqual(0, len(images))

    def test_admin_is_public_true_and_visibility_shared(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=True,
                                           filters={'visibility': 'shared'})
        self.assertEqual(0, len(images))

    def test_admin_is_public_false_and_visibility_shared(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=False,
                                           filters={'visibility': 'shared'})
        self.assertEqual(4, len(images))

    def test_admin_is_public_true_and_visibility_private(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=True,
                                           filters={'visibility': 'private'})
        self.assertEqual(0, len(images))

    def test_admin_is_public_false_and_visibility_private(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=False,
                                           filters={'visibility': 'private'})
        self.assertEqual(4, len(images))

    def test_admin_is_public_true_and_visibility_community(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=True,
                                           filters={'visibility': 'community'})
        self.assertEqual(0, len(images))

    def test_admin_is_public_false_and_visibility_community(self):
        images = self.db_api.image_get_all(self.admin_context, is_public=False,
                                           filters={'visibility': 'community'})
        self.assertEqual(4, len(images))

    def test_tenant1_is_public_true_and_visibility_public(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=True,
                                           filters={'visibility': 'public'})
        self.assertEqual(4, len(images))

    def test_tenant1_is_public_false_and_visibility_public(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=False,
                                           filters={'visibility': 'public'})
        self.assertEqual(0, len(images))

    def test_tenant1_is_public_true_and_visibility_shared(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=True,
                                           filters={'visibility': 'shared'})
        self.assertEqual(0, len(images))

    def test_tenant1_is_public_false_and_visibility_shared(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=False,
                                           filters={'visibility': 'shared'})
        self.assertEqual(1, len(images))

    def test_tenant1_is_public_true_and_visibility_private(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=True,
                                           filters={'visibility': 'private'})
        self.assertEqual(0, len(images))

    def test_tenant1_is_public_false_and_visibility_private(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=False,
                                           filters={'visibility': 'private'})
        self.assertEqual(1, len(images))

    def test_tenant1_is_public_true_and_visibility_community(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=True,
                                           filters={'visibility': 'community'})
        self.assertEqual(0, len(images))

    def test_tenant1_is_public_false_and_visibility_community(self):
        images = self.db_api.image_get_all(self.tenant1_context,
                                           is_public=False,
                                           filters={'visibility': 'community'})
        self.assertEqual(4, len(images))


class TestMembershipVisibility(test_utils.BaseTestCase):
    def setUp(self):
        super(TestMembershipVisibility, self).setUp()
        self.db_api = db_tests.get_db(self.config)
        db_tests.reset_db(self.db_api)
        self._create_contexts()
        self._create_images()

    def _create_contexts(self):
        self.owner1, self.owner1_ctx = self._user_fixture()
        self.owner2, self.owner2_ctx = self._user_fixture()
        self.tenant1, self.user1_ctx = self._user_fixture()
        self.tenant2, self.user2_ctx = self._user_fixture()
        self.tenant3, self.user3_ctx = self._user_fixture()
        self.admin_tenant, self.admin_ctx = self._user_fixture(admin=True)

    def _user_fixture(self, admin=False):
        tenant_id = str(uuid.uuid4())
        ctx = context.RequestContext(tenant=tenant_id, is_admin=admin)
        return tenant_id, ctx

    def _create_images(self):
        self.image_ids = {}
        for owner in [self.owner1, self.owner2]:
            self._create_image('not_shared', owner)
            self._create_image('shared-with-1', owner, members=[self.tenant1])
            self._create_image('shared-with-2', owner, members=[self.tenant2])
            self._create_image('shared-with-both', owner,
                               members=[self.tenant1, self.tenant2])

    def _create_image(self, name, owner, members=None):
        image = build_image_fixture(name=name, owner=owner,
                                    visibility='shared')
        self.image_ids[(owner, name)] = image['id']
        self.db_api.image_create(self.admin_ctx, image)
        for member in members or []:
            member = {'image_id': image['id'], 'member': member}
            self.db_api.image_member_create(self.admin_ctx, member)


class MembershipVisibilityTests(object):
    def _check_by_member(self, ctx, member_id, expected):
        members = self.db_api.image_member_find(ctx, member=member_id)
        images = [self.db_api.image_get(self.admin_ctx, member['image_id'])
                  for member in members]
        facets = [(image['owner'], image['name']) for image in images]
        self.assertEqual(set(expected), set(facets))

    def test_owner1_finding_user1_memberships(self):
        """Owner1 should see images it owns that are shared with User1."""
        expected = [
            (self.owner1, 'shared-with-1'),
            (self.owner1, 'shared-with-both'),
        ]
        self._check_by_member(self.owner1_ctx, self.tenant1, expected)

    def test_user1_finding_user1_memberships(self):
        """User1 should see all images shared with User1 """
        expected = [
            (self.owner1, 'shared-with-1'),
            (self.owner1, 'shared-with-both'),
            (self.owner2, 'shared-with-1'),
            (self.owner2, 'shared-with-both'),
        ]
        self._check_by_member(self.user1_ctx, self.tenant1, expected)

    def test_user2_finding_user1_memberships(self):
        """User2 should see no images shared with User1 """
        expected = []
        self._check_by_member(self.user2_ctx, self.tenant1, expected)

    def test_admin_finding_user1_memberships(self):
        """Admin should see all images shared with User1 """
        expected = [
            (self.owner1, 'shared-with-1'),
            (self.owner1, 'shared-with-both'),
            (self.owner2, 'shared-with-1'),
            (self.owner2, 'shared-with-both'),
        ]
        self._check_by_member(self.admin_ctx, self.tenant1, expected)

    def _check_by_image(self, context, image_id, expected):
        members = self.db_api.image_member_find(context, image_id=image_id)
        member_ids = [member['member'] for member in members]
        self.assertEqual(set(expected), set(member_ids))

    def test_owner1_finding_owner1s_image_members(self):
        """Owner1 should see all memberships of its image """
        expected = [self.tenant1, self.tenant2]
        image_id = self.image_ids[(self.owner1, 'shared-with-both')]
        self._check_by_image(self.owner1_ctx, image_id, expected)

    def test_admin_finding_owner1s_image_members(self):
        """Admin should see all memberships of owner1's image """
        expected = [self.tenant1, self.tenant2]
        image_id = self.image_ids[(self.owner1, 'shared-with-both')]
        self._check_by_image(self.admin_ctx, image_id, expected)

    def test_user1_finding_owner1s_image_members(self):
        """User1 should see its own membership of owner1's image """
        expected = [self.tenant1]
        image_id = self.image_ids[(self.owner1, 'shared-with-both')]
        self._check_by_image(self.user1_ctx, image_id, expected)

    def test_user2_finding_owner1s_image_members(self):
        """User2 should see its own membership of owner1's image """
        expected = [self.tenant2]
        image_id = self.image_ids[(self.owner1, 'shared-with-both')]
        self._check_by_image(self.user2_ctx, image_id, expected)

    def test_user3_finding_owner1s_image_members(self):
        """User3 should see no memberships of owner1's image """
        expected = []
        image_id = self.image_ids[(self.owner1, 'shared-with-both')]
        self._check_by_image(self.user3_ctx, image_id, expected)
