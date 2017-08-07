#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright: Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


ANSIBLE_METADATA = {'metadata_version': '1.0',
                    'status': ['preview'],
                    'supported_by': 'community'}


DOCUMENTATION = '''
---
module: postgresql_ext
short_description: Add or remove PostgreSQL extensions from a database.
description:
   - Add or remove PostgreSQL extensions from a database.
version_added: "1.9"
options:
  name:
    description:
      - name of the extension to add or remove
    required: true
    default: null
  version:
    description:
      - version of the extension to add or update to,
        has no effect when requested state is "absent", when not
        specified (with state "present") it will add latest
        available version
    required: false
    default: null
    version_added: "2.4"
  db:
    description:
      - name of the database to add or remove the extension to/from
    required: true
    default: null
  login_user:
    description:
      - The username used to authenticate with
    required: false
    default: null
  login_password:
    description:
      - The password used to authenticate with
    required: false
    default: null
  login_host:
    description:
      - Host running the database
    required: false
    default: localhost
  port:
    description:
      - Database port to connect to.
    required: false
    default: 5432
  state:
    description:
      - The database extension state
    required: false
    default: present
    choices: [ "present", "absent" ]
notes:
   - The default authentication assumes that you are either logging in as or sudo'ing to the C(postgres) account on the host.
   - This module uses I(psycopg2), a Python PostgreSQL database adapter. You must ensure that psycopg2 is installed on
     the host before using this module. If the remote host is the PostgreSQL server (which is the default case), then PostgreSQL must also be installed
     on the remote host. For Ubuntu-based systems, install the C(postgresql), C(libpq-dev), and C(python-psycopg2) packages on the remote host before using
     this module.
requirements: [ psycopg2 ]
author: "Daniel Schep (@dschep)"
'''

EXAMPLES = '''
# Adds postgis to the database "acme"
- postgresql_ext:
    name: postgis
    db: acme
'''
import traceback

try:
    import psycopg2
    import psycopg2.extras
    postgresqldb_found = True
except ImportError:
    postgresqldb_found = False

try:
    # quote_ident is available since Psycopg 2.7
    from psycopg2.extensions import quote_ident
    HAS_QUOTE_IDENT = True
except ImportError:
    HAS_QUOTE_IDENT = False

    import re
    class QuotedIdentifier(object):
        # See https://www.postgresql.org/docs/current/static/sql-syntax-lexical.html#SQL-SYNTAX-IDENTIFIERS
        EXTENSION_PATTERN = r'[^\W\d][\w_\$]+$'
        def __init__(self, identifier):
            self.identifier = identifier

        def getquoted(self):
            if re.match(self.EXTENSION_PATTERN, self.identifier):
                return self.identifier
            else:
                raise Exception('%r is not a valid identifier' % self.identifier)


from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils._text import to_native


class NotSupportedError(Exception):
    pass


def prepare_ext_query(cursor, query, params, name):
    if HAS_QUOTE_IDENT:
        query = query.format(ext=quote_ident(name, cursor))
    else:
        query = query.format(ext='%(ext)s')
        params['ext'] = QuotedIdentifier(name)
    return query


# ===========================================
# PostgreSQL module specific support methods.
#

def ext_exists(cursor, ext, ver=None):
    if ver:
        query = "SELECT * FROM pg_extension WHERE extname=%(ext)s and extversion=%(ver)s"
    else:
        query = "SELECT * FROM pg_extension WHERE extname=%(ext)s"
    cursor.execute(query, {'ext': ext, 'ver': ver})
    return cursor.rowcount == 1

def ext_delete(cursor, ext):
    if ext_exists(cursor, ext):
        query = "DROP EXTENSION {ext}"
        params = {}
        query = prepare_ext_query(cursor, query, params, ext)
        cursor.execute(query, params)
        return True
    else:
        return False

def ext_create(cursor, ext, ver=None):
    if not ext_exists(cursor, ext):
        if ver:
            query = 'CREATE EXTENSION {ext} VERSION %(ver)s'
        else:
            query = 'CREATE EXTENSION {ext}'

        params = {'ver': ver}
        query = prepare_ext_query(cursor, query, params, ext)
        cursor.execute(query, params)
        return True
    else:
        if ver:
            if not ext_exists(cursor, ext, ver):
                query = 'ALTER EXTENSION {ext} UPDATE TO %(ver)s'
                params = {'ver': ver}
                query = prepare_ext_query(cursor, query, params, ext)
                cursor.execute(query, params)
                return True
        return False

# ===========================================
# Module execution.
#

def main():
    module = AnsibleModule(
        argument_spec=dict(
            login_user=dict(default="postgres"),
            login_password=dict(default="", no_log=True),
            login_host=dict(default=""),
            port=dict(default="5432"),
            db=dict(required=True),
            ext=dict(required=True, aliases=['name']),
            version=dict(default=""),
            state=dict(default="present", choices=["absent", "present"]),
        ),
        supports_check_mode = True
    )

    if not postgresqldb_found:
        module.fail_json(msg="the python psycopg2 module is required")
    elif not HAS_QUOTE_IDENT:
        psycopg2.extensions.register_adapter(QuotedIdentifier, lambda identifier: identifier)

    db = module.params["db"]
    ext = module.params["ext"]
    version = module.params["version"]
    port = module.params["port"]
    state = module.params["state"]
    changed = False

    # To use defaults values, keyword arguments must be absent, so
    # check which values are empty and don't include in the **kw
    # dictionary
    params_map = {
        "login_host":"host",
        "login_user":"user",
        "login_password":"password",
        "port":"port"
    }
    kw = dict( (params_map[k], v) for (k, v) in module.params.items()
              if k in params_map and v != '' )
    try:
        db_connection = psycopg2.connect(database=db, **kw)
        # Enable autocommit so we can create databases
        if psycopg2.__version__ >= '2.4.2':
            db_connection.autocommit = True
        else:
            db_connection.set_isolation_level(psycopg2
                                              .extensions
                                              .ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = db_connection.cursor(
            cursor_factory=psycopg2.extras.DictCursor)
    except Exception as e:
        module.fail_json(msg="unable to connect to database: %s" % to_native(e), exception=traceback.format_exc())

    try:
        if module.check_mode:
            if state == "present":
                changed = not ext_exists(cursor, ext, version)
            elif state == "absent":
                changed = ext_exists(cursor, ext)
        else:
            if state == "absent":
                changed = ext_delete(cursor, ext)

            elif state == "present":
                changed = ext_create(cursor, ext, version)
    except NotSupportedError as e:
        module.fail_json(msg=to_native(e), exception=traceback.format_exc())
    except Exception as e:
        module.fail_json(msg="Database query failed: %s" % to_native(e), exception=traceback.format_exc())

    module.exit_json(changed=changed, db=db, ext=ext)


if __name__ == '__main__':
    main()
