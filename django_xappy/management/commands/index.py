import logging
from optparse import make_option
from django.core.management.base import BaseCommand, CommandError
from django_xappy import update

class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--full-rebuild', action='store_true',
            dest='rebuild', default=None,
            help='Completely rebuild the index from scratch. You should '
                 'only need to run this if you make changes to the index '
                 'format or the app\'s search code itself, e.g. add new '
                 'fields, add new models to be indexed etc.'),

        make_option('--update', action='store_true',
            dest='update', default=None,
            help='Handle changed records since last update. '
                 'This brings the index up-to-date with the changes '
                 'flagged in the database.'),
    )
    help = "Update the search index."

    def handle(self, *args, **options):
        verbosity = int(options.get('verbosity', 0))
        if verbosity > 1:
            update.log.setLevel(logging.DEBUG)
        elif verbosity < 1:
            update.log.setLevel(logging.WARNING)

        if options.get('rebuild'):
            update.rebuild(clear_changes=True)
        elif options.get('update'):
            update.apply_changes()
        else:
            raise CommandError("You need to specify either --update or "
                "--full-rebuild")