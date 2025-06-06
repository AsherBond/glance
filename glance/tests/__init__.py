# Copyright 2010-2011 OpenStack Foundation
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

import builtins

import eventlet
eventlet.patcher.monkey_patch()

import glance.async_
# NOTE(danms): Default to eventlet threading for tests
glance.async_.set_threadpool_model('eventlet')

# See http://code.google.com/p/python-nose/issues/detail?id=373
# The code below enables tests to work with i18n _() blocks
setattr(builtins, '_', lambda x: x)

# Set up logging to output debugging
import logging
logger = logging.getLogger()
hdlr = logging.FileHandler('run_tests.log', 'w')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.DEBUG)
