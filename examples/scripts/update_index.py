"""Example of a custom update script.

Xou may also use the management command instead.
"""

# setup django first
from os import path
import sys
sys.path.append(path.normpath(path.join(path.dirname(__file__), '..')))

import settings
from django.core.management import setup_environ
setup_environ(settings)

import search.models

if __name__ == '__main__':
    from django_xappy import update
    update.main()