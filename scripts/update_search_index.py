__doc__ = """
%(scriptname)s [options]

Main Options:
    --update
        Handle changed records since last update. This brings the index
        up-to-date with the changes flagged in the database.

    --full-rebuild
        Completely rebuild the index from scratch. You should only need to run
        this if you make changes to the index format or the app's search code
        itself, e.g. add new fields, add new models to be indexed etc. Apart
        from that, the normal incremental update mechanism should work
        flawlessly.

Other Options:
    -h,--help           print usage info (this)
    -q                  be extra quiet
    -v                  be extra verbose
"""
# TODO: support unapproved items, which will not be included in the index

from pyutils import setup_django
setup_django('./../..')

import sys, os, shutil, datetime, time, getopt
from pyutils import cmdline
from pyutils.statements import switch
from django.conf import settings
from django_xappy.models import Change
from django_xappy.index import index, IndexData

def help(argv):
    return cmdline.help(__doc__, argv)

def main(argv=None):
    try:
        opts, args = getopt.getopt(argv[1:], 'hqv',
                                   ['full-rebuild', 'update', 'help'])
    except getopt.GetoptError, e:
        return cmdline.error(e)
    if args:
        return cmdline.error('Commands not supported: %s' % ", ".join(args))
    for o, a in opts:
        for case in switch(o):
            if case('-h', '--help'):
                return help(argv)
            elif case('-v'):
                cmdline.options.verbose = True
            elif case('-q'):
                cmdline.options.quiet = True
            elif case('--full-rebuild'):
                cmdline.options.full = True
            elif case('--update'):
                cmdline.options.changes = True
            elif case():
                assert False, "unhandled option"

    # rebuild the whole index
    if cmdline.options.full:
        # As this may take a while, we create the index in a temporary
        # location, and then switch it with the currently active one, and
        # finally deleting the latter.
        old_location = settings.SEARCH_INDEX_LOCATION
        settings.SEARCH_INDEX_LOCATION += "-%s" % int(time.time())
        index.reopen()
        # Remember the current time so that we can remove all change items
        # up to this point if we are successful.
        indexing_started_at = datetime.datetime.now()

        # index everything
        try:
            cmdline.message('Creating a new index in "%s"...' % \
                os.path.basename(index.path))
            for model in IndexData.models:
                cmdline.message('Indexing %d objects of type "%s"...' % \
                    (model.objects.count(), model.__name__))
                for obj in model.objects.all():
                    cmdline.verbose('\t#%d: %s' % (obj.pk, str(obj)))
                    index.add(obj)
        finally:
            index.flush()

        # switch the live index with the temporary one we just created
        index.close()
        cmdline.message('Switching "%s" to live index...'%os.path.basename(index.path))
        try:
            if os.path.exists(old_location):
                shutil.rmtree(old_location)
            shutil.move(settings.SEARCH_INDEX_LOCATION, old_location)
        except Exception, e:
            cmdline.error("Failed to replace live index, error was: %s"%e)
        else:
            cmdline.message('Done.')
        settings.SEARCH_INDEX_LOCATION = old_location

        # Since this was a complete reindex, we can assume that existing
        # changelog entries are obsolete. Note however that we:
        #   - only delete after the reindex was successful
        #   - only delete items up to the point we started the indexing.
        #     changes that happend in the meantime did possibly not make
        #     it into the new index
        old_changes = Change.objects.before(indexing_started_at)
        if old_changes:
            cmdline.message('Deleting %d now obsolete item(s) from changelog.'
                % old_changes.count())
            old_changes.delete()

    # update changed records
    elif cmdline.options.changes:
        try:
            cmdline.message('Updating index with %d changes...'%Change.objects.count())
            # note that the order is important
            for change in Change.objects.ordered():
                _chstr = {
                    Change.Kind.add: 'added',
                    Change.Kind.update: 'updated',
                    Change.Kind.delete: 'deleted',
                }
                cmdline.verbose('\t#%d of type "%s" was %s' % (
                    change.object_id, change.content_type, _chstr[change.kind]),
                    header='current-change')
                for case in switch(change.kind):
                    if case(Change.Kind.delete):
                        index.delete(change.object_id,
                                     content_type=change.content_type)
                        break;

                    if case(Change.Kind.add, Change.Kind.update):
                        try:
                            index.update(change.content_object)
                        except change.model.DoesNotExist:
                            # Handle db objects gracefully. The reason this
                            # should not happen is that a "delete" action
                            # should normally be logged as well and is handled
                            # separately.
                            cmdline.warning('\tSkipping - the database record '
                                'associated with this change does no longer '
                                'exist. This should normally not happen.',
                                depends="current-change")
                        break;

                    if case():
                        assert False, "unknown change kind"
                # done & gone
                change.delete()
            cmdline.message('Done.')
        finally:
            index.flush()
    else:
        return help(argv)

if __name__ == '__main__':
    sys.exit(main(sys.argv) or 0)