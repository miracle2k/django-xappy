"""Provides write functionality to the search index, i.e. add, update,
delete etc.

What fields of what models are indexed, and how so, is also defined here.
"""

import xappy
from django.db import models
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from models import Searchable


def mkdocid(obj_id, type_id):
    """
    Format a search index document id, based on a content type (``type_id``)
    and an instance id (``obj_id``).
    """
    return "%d_%d" % (type_id, obj_id)


class IndexData(object):
    """
    If the search index spans multiple models like in our case, the official
    Django search-api branch, as well as the djapian project, use a proxy model
    that mirrors all documents in the index. For an example, see:
        http://code.google.com/p/djapian/wiki/IndexingManyModelsAtOnce

    However, to avoid the maintenance overhead of an additional model with
    it's own rows for all the other models' rows (including scripts to resync
    that model just in case), we are using a different approach.

    While this class looks pretty much like such a proxy model, and tries to
    enable the relevant usage patterns (which has the added bonus that we would
    be able to switch to the default approach relatively painlessly), it is in
    fact not a model.

    Create an instance of this class with a ``content_object`` will provide
    the data for the fields in the search index via attributes. If you want to
    change what models are indexed, which fields and how so, you'll have to
    edit this class. The ``fields`` attribute lists what fields are available
    in the index, and the property getter functions decide how the different
    models map to those fields.

    Note that the Django search-api branch (i.e. also djapian) does not store
    the field definitions here; they are passed to the constructor of the
    indexer object. However, this class here seems like a better fit for our
    situation.
    """

    fields = {}

    class __metaclass__(type):
        @property
        def models(self):
            """
            List all models supported by search; This is used, for example,
            to determine which models to use when rebuilding the complete
            index.
            """
            return Searchable.get_models()

    def __init__(self, content_object):
        self.content_object = content_object
        self.content_type = ContentType.objects.get_for_model(content_object)
        self.object_id = content_object.pk

    def _is(self, *models):
        for model in models:
            if isinstance(self.content_object, model):
                return True
        return False

    @property
    def document_id(self):
        """
        Note that this is is not an actual field, it's the real id of the
        document in the search index, and different from ``id``. While ``id``
        stores the ID of the instance of the model in question, ``document_id``
        has to combine the instance id and information about the model to be
        unique.
        """
        return mkdocid(self.obj_id, self.ctype_id)


class XapianIndexer(object):
    """Light wrapper around a Xapian index. Late binding ensures that
    the index is only opened when necessary.
    """
    def __init__(self):
        self._connection = None

    @property
    def path(self):
        return settings.SEARCH_INDEX_LOCATION

    def __connect(self):
        """
        Ensure a xappy connection exists. This makes sure we connect only once,
        but also only when necessary; requests that do not need search will
        never have to touch the index.
        """
        if not self._connection:
            self._connection = xappy.IndexerConnection(self.path)
            # Register fields and their actions; note that because those are
            # stored as meta data with the index itself, we only add them the
            # first time the index is created. We assume that it was created
            # if there are no actions at all defined.
            if not self._connection.get_fields_with_actions():
                for field, actions in IndexData.fields.items():
                    for action in actions:
                        # normalize the action object, can be listed in
                        # different ways depending on what data is given.
                        if not isinstance(action, tuple):
                            action = (action, {},)
                        elif len(action) == 1:
                            action = (action[0], {})

                        self._connection.add_field_action(
                            field, action[0], **action[1])

    def _document_for_instance(self, instance):
        """Return a ``UnprocessedDocument`` for the model instance, which
        can then be added to the index.

        Used by the ``add`` and ``update`` methods.
        """
        data = IndexData(content_object=instance)
        document = xappy.UnprocessedDocument()
        # set the id
        document.id = data.document_id
        # add all other fields
        for field in data.fields:
            value = getattr(data, field)
            # field is apparently not available for this model
            if value is None: continue;

            # we need an utf8-encoded string for xapian
            if isinstance(value, basestring):
                pass
            elif isinstance(value, (int, long)):
                value = u"%d" % value
            else:
                # hope for the best
                value = unicode(value)
            # if we already have a string, assume it's already utf-8
            if not isinstance(value, str):
                value = value.encode('utf8')
            document.fields.append(xappy.Field(field, value))
        return document

    @property
    def length(self):
        """Return total number of documents in the index."""
        self.__connect()
        self._connection.get_doccount()

    def add(self, instance):
        """
        Add a document to the index; currently synonymous with ``update``, as
        our documents (model instances) already have an ID in every case.
        """
        return self.update(instance)

    def update(self, instances):
        """
        Update one or multiple documents in the index; If they do not exist
        yet, they are added.
        """
        if not isinstance(instances, (list, tuple)):
            instances = (instances,)

        self.__connect()
        for instance in instances:
            return self._connection.replace(self._document_for_instance(instance))

    def delete(self, id, model=None, content_type=None):
        """
        Delete a document from the index; silently fails if it doesn't exist
        in the first place.

        You can just pass a model instance for ``id``, but as object is usually
        missing from the database once this is called, it will be an integer
        most of the time, and you will have to pass either a model class or
        a content type so that the document id can be built.
        """
        if isinstance(id, models.Model):
            model = type(id)
            id = id.pk

        assert model or content_type,\
            "You need to specify either a model or a content type"

        if not content_type:
            content_type = ContentType.objects.get_for_model(model)
        self.__connect()
        self._connection.delete(mkdocid(id, content_type.id))

    def flush(self):
        if self._connection:
            self._connection.flush()
    def close(self):
        if self._connection:
            self._connection.close()
            self._connection = None
    def reopen(self):
        self.close()
        self.__connect()

index = XapianIndexer()