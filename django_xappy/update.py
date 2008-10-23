import os, sys
import datetime, time
import logging

from django.conf import settings
from models import Change

from index import get_indexes


# setup output
console = logging.StreamHandler(sys.stdout)
log = logging.getLogger('django_xappy.update')
log.addHandler(console)
log.setLevel(logging.INFO)



def rebuild(indexes=None, clear_changes=False):
    """Fully rebuild indixes from scratch, based on current database.

    You should only need to run this if you make changes to the index
    format, e.g. add new fields or new models to be indexed. Apart from
    that, the normal incremental update mechanism should keep database
    and index in sync.

    You may pass the indexes you want to rebuild as a tuple, otherwise
    the list of all registered indexes will be used. Note that the
    latter requires your index classes to already be loaded by Python.

    If you set ``clear_changes`` to True, the changelog will be cleared
    after the rebuild. Only do this if you are rebuilding all indexes,
    or changes affecting the other indexes may be lost.
    """

    import shutil

    if not indexes:
        indexes = get_indexes()
    elif not isinstance(indexes, (list, tuple)):
        indexes = (indexes,)

    # remember the current time so that we can remove all changes
    # up to this point if we are successful
    indexing_started_at = datetime.datetime.now()

    for index_klass in indexes:
        # as this may take a while, we create the index in a temporary
        # location, and then switch it with the currently active one,
        # finally deleting the latter
        temp_index = index_klass(index_klass.location+"-%s" % int(time.time()))

        # index everything
        try:
            log.info('Creating a new index in "%s"...' % \
                os.path.basename(temp_index.location))
            for model, queryset in temp_index.get_models(True):
                log.info('Indexing %d objects of type "%s"...' % \
                    (queryset.count(), model.__name__))
                for obj in queryset.all():
                    log.debug('\t#%d: %s' % (obj.pk, str(obj)))
                    temp_index.add(obj)
        finally:
            temp_index.flush()

        # switch the live index with the temporary one we just created
        temp_index.close()
        log.info('Switching "%s" to live index...' % os.path.basename(temp_index.location))
        try:
            if os.path.exists(index_klass.location):
                shutil.rmtree(index_klass.location)
            shutil.move(temp_index.location, index_klass.location)
        except Exception, e:
            log.error("Failed to replace live index, error was: %s"%e)
        else:
            log.info('Done.')

    # Since this was a complete reindex, we can assume that existing
    # changelog entries are obsolete. Note however that we:
    #   - only delete after the reindex was successful
    #   - only delete items up to the point we started the indexing.
    #     changes that happend in the meantime did possibly not make
    #     it into the new index
    if clear_changes:
        old_changes = Change.objects.before(indexing_started_at)
        if old_changes:
            log.info('Deleting %d now obsolete item(s) from changelog.'
                % old_changes.count())
            old_changes.delete()


def apply_changes():
    """Apply logged model changes to search indexes.

    While ``rebuild`` may be run on a specific set of indexes, due to the
    way changes are stored, this always needs to handle all your indexes.
    There'd be no way to determine which changes have already been applied
    to which index.
    """

    # connect to every index
    indexes = []
    for index_klass in get_indexes():
        indexes.append(index_klass())

    try:
        log.info('Updating %d %s with %d changes...' % (
                    len(indexes),
                    len(indexes) == 1 and 'index' or 'indexes',
                    Change.objects.count()))
        for change in Change.objects.ordered():
            _chstr = {
                Change.Kind.add: 'added',
                Change.Kind.update: 'updated',
                Change.Kind.delete: 'deleted',
            }

            # apply this change to all indexes it is relevant to
            count_affected = 0
            for index in indexes:
                if not index.is_reponsible(change):
                    continue
                else:
                    count_affected += 1

                if change.kind == Change.Kind.delete:
                    # note it is possible that the object doesn't even
                    # exist, if it was deleted after being created, before
                    # we even did the first update.
                    index.delete(change.object_id,
                                 content_type=change.content_type)

                elif change.kind in (Change.Kind.add, Change.Kind.update):
                    try:
                        index.update(change.content_object)
                    except change.model.DoesNotExist:
                        # Handle db objects gracefully. The reason this
                        # should not happen is that a "delete" action
                        # should normally be logged as well and is handled
                        # separately.
                        log.warning('\tSkipping %s #%d - the database record '
                            'associated with this change does no longer '
                            'exist. This should normally not happen.',
                                change.content_type, change.object_id)
                        continue  # iterations for other indexes will fail too

                else:
                    assert False, "unknown change kind"

            log.debug('\t#%d of type "%s" was %s to %s index%s' % (
                change.object_id, change.content_type, _chstr[change.kind],
                count_affected, 'es' if count_affected != 1 else ''))

            # this change is now handled
            change.delete()

    finally:
        for index in indexes:
            index.flush()

    log.info('Done.')


def main(argv=None):
    """Provides a simple "update index" commandline script that you
    can easily wrap around in a custom script file:

        from django_xappy import update
        if __name__ == '__main__':
            update.main()

    Don't forget to setup a working Django environent beforehand
    though.
    """

    if argv is None:
        argv = sys.argv

    import getopt
    try:
        opts, args = getopt.getopt(argv[1:], 'hqv',
                                   ['full-rebuild', 'update', 'help'])
    except getopt.GetoptError, e:
        return log.error(e)
    if args:
        return log.error('Commands not supported: %s' % ", ".join(args))

    full_rebuild = update_only = False
    for o, a in opts:
        if o in ('-h', '--help'):
            pass
        elif o == '-v':
            log.setLevel(logging.DEBUG)
        elif o == '-q':
            log.setLevel(logging.WARNING)
        elif o == '--full-rebuild':
            full_rebuild = True
        elif o == '--update':
            update_only = True
        else:
            assert False, "unhandled option"

    if full_rebuild:
        rebuild(clear_changes=True)
    elif update_only:
        apply_changes()
    else:
        print """%(scriptname)s [options]

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
    -v                  be extra verbose""" % {'scriptname': os.path.basename(argv[0])}