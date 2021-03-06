"Configuration."

import os
import os.path

from . import constants
from . import utils

ROOT = os.path.dirname(os.path.abspath(__file__))

# Default configurable values; modified by reading a JSON file in 'init'.
DEFAULT_SETTINGS = dict(
    ROOT = ROOT,
    SERVER_NAME = '127.0.0.1:5003',
    SITE_NAME = 'Anubis',
    SITE_STATIC_DIRPATH = None, # Must NOT be same as 'Anubis/site'! Security!
    SITE_ICON = None,           # Filename, must be in 'SITE_STATIC_DIRPATH'
    SITE_LOGO = None,           # Filename, must be in 'SITE_STATIC_DIRPATH'
    SITE_DESCRIPTION = "Proposal submission and review handling system.",
    DEBUG = False,
    HOST_LOGO = None,           # Filename, must be in 'SITE_STATIC_DIRPATH'
    HOST_NAME = None,
    HOST_URL = None,
    SECRET_KEY = None,          # Must be set in 'settings.json'
    SALT_LENGTH = 12,
    COUCHDB_URL = 'http://127.0.0.1:5984/',
    COUCHDB_USERNAME = None,
    COUCHDB_PASSWORD = None,
    COUCHDB_DBNAME = 'anubis',
    JSON_AS_ASCII = False,
    JSON_SORT_KEYS = False,
    MIN_PASSWORD_LENGTH = 6,
    PERMANENT_SESSION_LIFETIME = 7 * 24 * 60 * 60, # seconds; 1 week
    DOC_DIRPATH = os.path.join(ROOT, 'documentation'),
    MAIL_SERVER = 'localhost',
    MAIL_PORT = 25,
    MAIL_USE_TLS = False,
    MAIL_USERNAME = None,
    MAIL_PASSWORD = None,
    MAIL_DEFAULT_SENDER = 'anubis@your.org', # Must be changed in settings!
    CALL_IDENTIFIER_MAXLENGTH = 10,
    CALL_REMAINING_DANGER = 1.0,
    CALL_REMAINING_WARNING = 7.0,
    CALLS_OPEN_ORDER_KEY = 'closes',
    USER_GENDERS = ['male', 'female', 'other'],
    USER_BIRTHDATE = True,
    USER_TITLE = True,
    USER_AFFILIATION = True,
    USER_POSTAL_ADDRESS = True,
    USER_PHONE = True,
    USER_ENABLE_IMMEDIATELY = False,
    USER_ENABLE_EMAIL_WHITELIST = [], # List of fnmatch patterns, not regexp's!
    MARKDOWN_URL = 'https://daringfireball.net/projects/markdown/syntax',
)


def init(app, filepath=None):
    """Perform the configuration of the Flask app.
    Set the defaults, and then read the first JSON settings file found:
    1) the argument to this procedure.
    2) The environment variable ANUBIS_SETTINGS.
    3) The file 'settings.json' in this directory.
    4) The file '../site/settings.json' relative to this directory.
    Check the environment for a specific set of variables and use if defined.
    Raise IOError if settings file could not be read.
    Raise KeyError if a settings variable is missing.
    Raise ValueError if a settings variable value is invalid.
    """
    # Set the defaults specified above.
    app.config.from_mapping(DEFAULT_SETTINGS)
    # Modify the configuration from a JSON settings file.
    filepaths = []
    if filepath:
        filepaths.append(filepath)
    try:
        filepaths.append(os.environ['ANUBIS_SETTINGS'])
    except KeyError:
        pass
    for filepath in ['settings.json', '../site/settings.json']:
        filepaths.append(os.path.normpath(os.path.join(ROOT, filepath)))
    for filepath in filepaths:
        try:
            app.config.from_json(filepath)
        except FileNotFoundError:
            pass
        else:
            app.config['SETTINGS_FILE'] = filepath
            break
    # Modify the configuration from environment variables.
    for key, convert in [('DEBUG', utils.to_bool),
                         ('SECRET_KEY', str),
                         ('COUCHDB_URL', str),
                         ('COUCHDB_USERNAME', str),
                         ('COUCHDB_PASSWORD', str),
                         ('MAIL_SERVER', str),
                         ('MAIL_SERVER', str),
                         ('MAIL_USE_TLS', utils.to_bool),
                         ('MAIL_USERNAME', str),
                         ('MAIL_PASSWORD', str),
                         ('MAIL_DEFAULT_SENDER', str)]:
        try:
            app.config[key] = convert(os.environ[key])
        except (KeyError, TypeError, ValueError):
            pass
    # Sanity check; should not execute if this fails.
    assert app.config['SECRET_KEY']
    assert app.config['SALT_LENGTH'] > 6
    assert app.config['MIN_PASSWORD_LENGTH'] > 4
