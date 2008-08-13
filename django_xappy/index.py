import time
import types

from django.db import models
from django.utils.safestring import mark_safe
from django.contrib.contenttypes.models import ContentType
import xappy
import xappy.searchconnection

from models import log_model
from utils import template_callable


__all__ = ('action', 'Index', 'FieldActions')


# make available here so user's don't have to import from xappy
from xappy import FieldActions


def action(fieldtype, **kwargs):
    """Define a field action for the decorated data field of the index.

    Replaces the native ``indexer_connection.add_field_action()`` calls.

    Only data fields that have at least one action associated with them
    are recognized.

    Example:

        class MyIndex(Index):
            class Data:
                @action(xappy.FieldActions.INDEX_FREETEXT, language="en")
                @action(xappy.FieldActions.STORE_CONTENT)
                def myfield():
                    pass
    """

    def decorator(func):
        if not hasattr(func, '_actions'):
            func._actions = []
        func._actions.append(tuple((fieldtype, kwargs,)))
        return func
    return decorator


class IndexDataBase(object):
    """Base class for index inner ``Data`` classes.

    Provides functionality that the ``Data`` class can use to define
    it's fields.

    Is injected automatically by ``IndexMetaclass``.
    """

    @classmethod
    def get_fields(cls):
        """Return the fields defined by this data class (as their name).
        """
        for name in dir(cls):
            obj = getattr(cls, name)
            # only methods with at least one actions are considered fields
            if isinstance(obj, types.MethodType):
                if len(getattr(obj.im_func, '_actions', [])) > 0:
                    yield name

    @classmethod
    def get_fieldactions(cls):
        """Return all actions defined by this class, together with
        their respective field name.
        """
        for name in cls.get_fields():
            obj = getattr(cls, name)
            for action  in getattr(obj.im_func, '_actions'):
                yield name, action

    def __init__(self, content_object=None, content_type=None, object_id=None):
        """Create an instance of the proxy data class, wrapping around
        a model instance.

        Specifify the wrapped object by passing ``content_object``.

        Alternatively, you may pass the content type and the object id.
        This is necessary when the object in question does no longer
        exist, and exists primarily to allow the ``delete()`` operation
        to get access to the ``document_id()`` builder.
        """

        assert not (content_object and (content_type or object_id)), \
            "Cannot specify both content_object and content_type/object_id"

        assert content_object or (content_type and object_id), \
            "Either content_object or content_type/object_id is required"

        if content_object:
            self.content_object = content_object
            self.content_type = ContentType.objects.get_for_model(content_object)
            self.object_id = content_object.pk
        else:
            self.content_type = content_type
            self.object_id = object_id
            # We could try to query for a content_object, but the only
            # case where this __init__ version is currently used is by
            # delete(), and the object wouldn't exist anymore, anyway.
            # So, just set to None.
            self.content_object = None

    def __eq__(self, other):
        """Shortcut to allow subclasses to check if the content object
        they represent belongs to a certain model.

            if self == auth.User:
                ...
            if self == (Book, auth.User):
                ...

        The second version checks if either of the types in the tuple
        applies.
        """
        for model in isinstance(other, (tuple, list)) and other or (other,):
            if isinstance(self.content_object, model):
                return True
        return False

    def document_id(self):
        """Provides the id for the search index document that will
        represent the object this instance wraps around.

        It behaves like a field, but is not in fact an actual field,
        and no actions should be applied to it.

        Note that while this method may be overriden by a user's custom
        index data class in the exact same way that other fields can be
        added, this is currently not recommended. The code that resolves
        search results to model instances relies on ths particular
        document id format!

        Don't confuse this with the wrapped object's id
        (``content_object.id``). Since the index may store objects from
        multiple models, to ensure a unique id for each document, it
        needs to be combined with data identifying the model.
        """
        return "%d-%d" % (self.object_id, self.content_type.pk)


# Simple registry keeping track of all indexes defined. This is managed
# by the index metaclass and used for example by the update scripts to
# know which indexes they need to write to.
_INDEX_REGISTRY = []

def get_indexes():
    return _INDEX_REGISTRY


class IndexMetaclass(type):
    """Injects ``IndexDataBase`` into the inner ``Data`` class of an
    index.

    This basically spares the user from having to subclass the inner
    Data classes manually.
    """

    def __new__(cls, name, bases, attrs):
        try:
            Index
            defining_self = False
        except NameError:
            defining_self = True

        if not defining_self:
            if not 'location' in attrs:
                raise RuntimeError('You need to specify the filesystem '
                    'location of the index.')

            dataklass = attrs.pop('Data', None)
            if dataklass is None:
                raise RuntimeError('Index needs an inner Data class.')
            # assigning to __bases__ is complicated (although in theory
            # possible); isntead, we simply create a new container type
            # inheriting from both the user's Data class and the base,
            # simulating direct inheritance quite well.
            attrs['Data'] = type("%sData"%name, (dataklass, IndexDataBase), {})

        klass = type.__new__(cls, name, bases, attrs)

        if not defining_self:
            if not klass in _INDEX_REGISTRY:
                _INDEX_REGISTRY.append(klass)

        return klass


class Index(object):
    """Acts both as a base class during index definition, as well as
    representing an index connection when instantiated.

    Connections are late-bound; they are not established until absolutely
    necessary; requests that do not need search will never have to touch
    the index, even if you already created an instance.
    """

    __metaclass__ = IndexMetaclass


    ## Class-usage

    _models = []   # list of models registered with this index

    @classmethod
    def register(cls, model):
        """Register a model with this index.

        Changes to this model will then be logged in the database, and
        applied to the index during the update process.
        """
        if not model in cls._models:
            cls._models.append(model)
        log_model(model)

    @classmethod
    def get_models(cls):
        return cls._models


    ## Instance-usage

    def __init__(self, location=None):
        """
        If ``location`` is not specified, the value will be inherited
        from the location specified when defining the index class.
        """
        if location:
            self.location = location
        self._indexer = None
        self._searcher = None

    def _connect_searcher(self):
        if not self._searcher:
            self._searcher = xappy.SearchConnection(self.location)

    def _connect_indexer(self):
        if not self._indexer:
            self._indexer = xappy.IndexerConnection(self.location)

            # First time the index is created, register fields and their
            # actions; The index is assumed to have been creating if
            # there are no current field actions.
            if not self._indexer.get_fields_with_actions():
                for field, action in self.Data.get_fieldactions():
                    # normalize the action object, can be given in
                    # different ways depending on what data is needed
                    if not isinstance(action, tuple):
                        action = (action, {},)
                    elif len(action) == 1:
                        action = (action[0], {})

                    self._indexer.add_field_action(
                        field, action[0], **action[1])


    ## Common

    def length(self):
        """Return total number of documents in the index.
        """
        if self._indexer:
            return self._indexer.get_doccount()
        else:
            if not self._searcher:
                self._connect_searcher()
            return self._searcher.get_doccount()

    def close(self):
        if self._indexer:
            self._indexer.close()
            self._indexer = None
        if self._searcher:
            self._searcher.close()
            self._searcher = None


    ## Indexing

    def _document_for_instance(self, instance):
        """Return an ``UnprocessedDocument`` for the given model
        instance, which can then be added to the index.

        Used by the ``add`` and ``update`` methods.
        """

        data = self.Data(content_object=instance)
        document = xappy.UnprocessedDocument()
        document.id = data.document_id()

        for field in data.get_fields():
            value = getattr(data, field)()
            if value is None:
                # apparently not available for this object/model
                continue

            # we need an utf8-encoded string for xapian
            if isinstance(value, unicode):
                value = value.encode('utf8')
            if isinstance(value, str):
                # existing strings are simply assumed to be utf8
                pass
            elif isinstance(value, (int, long)):
                value = u"%d" % value

            document.fields.append(xappy.Field(field, value))

        return document


    def add(self, instance):
        """Add a document to the index.

        Currently synonymous with ``update``, since our documents (model
        instances) already have an ID in every case.
        """
        return self.update(instance)


    def update(self, instances):
        """Update one or multiple documents in the index.

        Objects that do not yet exist exist as documents are added.
        """
        if not isinstance(instances, (list, tuple)):
            instances = (instances,)

        self._connect_indexer()
        for instance in instances:
            return self._indexer.replace(self._document_for_instance(instance))


    def delete(self, what, model=None, content_type=None):
        """Delete a document from the index.

        Silently fails if it doesn't exist in the first place.

        ``what`` can simply be a model instance but since the object to
        be deleted usually doesn't exist anymore once this is called, you
        can also pass an id in conjection with either a model class or
        the content type.
        """

        if isinstance(what, models.Model):
            doc = self.Data(content_object=what)

        else:
            assert model or content_type,\
                "You need to specify either a model or a content type"
            if not content_type:
                content_type = ContentType.objects.get_for_model(model)

            doc = self.Data(content_type=content_type, object_id=what)

        self._connect_indexer()
        self._indexer.delete(doc.document_id())


    def flush(self):
        if self._indexer:
            self._indexer.flush()


    ## Searching

    def search(self, query, page=1, num_per_page=10, order_by=None,
               adjust_page=False, **kwargs):
        """Do a search for ``query``.

        ``query`` is a Google-syntax like search string, as supported
        by Xappy.

        Instead of specifying a start- and end-index, you pass the
        ``page`` (1-based), and the number of items per page
        (``num_per_page``) to this method. Not only will the caller have
        less work to do, it also makes it possible for us to help with
        pagination:

        See, Xapian does not always provide an exact estimate, so code
        that uses a paginator when outputting the page might show pages
        that do not actually exist. If the user then requests one of
        those, the whole thing shouldn't break down. Instead, the right
        thing would be to gracefully display the last page of results.

        This method detects this situation (a non-existant page being
        requested), and instead returns the last page of results.
        Additionally, it can notify you of this page: If you set the
        ``adjust_page`` argument to True, the result will a 2-tuple
        **(results, fixed_page_num)**.

        Pass ``False`` to ``order_by``, if you want the default sort
        order by relevance. Otherwise, pass the field name.

        All other **kwargs will be passed on the Xappy's ``search``
        method. For example, you may use it to enable the
        ``getfacets`` option.
        """

        self._connect_searcher()
        conn = self._searcher

        start = (page-1)*num_per_page
        count = num_per_page
        query_utf8 = query.encode('utf-8')

        ts_begin = time.time()

        q = conn.query_parse(query_utf8)
        _search = lambda s:\
            conn.search(q, s, s+count, sortby=order_by, **kwargs)

        # first, attempt a normal search
        results = _search(start)

        # Check for the case that a non-existant page number was
        # requested, likely because we reported a too high result count
        # in earlier searches. Handle gracefully and repeat the search
        # to retrieve to last page of results.
        #
        # Note that this is only possible because now we DO have the
        # exact number of hits, because Xapian went through the whole
        # resultset on the search we just did.
        if results.matches_estimated<start+1 and results.estimate_is_exact:
            page = (results.matches_estimated/count)+1
            start = (page-1)*num_per_page
            results = _search(start)

        search_time = time.time() - ts_begin

        results = XapianResults(
                    results,
                    offset=start,
                    query=query,
                    search_time=search_time)

        if adjust_page:
            return results, page
        else:
            return results


class XapianResults(object):
    """A thin wrapper around the ``SearchResults`` object returned
    by Xappy, exposing the functionality we care about most, and
    also wrapping each result in a ``XapianResult`` class.
    """

    def __init__(self, results, offset, query, search_time=None):
        """
        The number in ``offset`` specifies the first index of the
        search results, 0-based (e.g. for results 31-40, offset
        will be 30).
        """
        self._results = results
        self.offset = offset
        self.query = query
        self.search_time = search_time

    def _mkresult(self, index):
        """Wrap a xappy search result with our own custom class.

        In addition, this method serves two important functions:

        Firstly, it resolves each search results to a model instance,
        which it makes available via the ``content_object`` attribute.

        Also, this is the place were we handle sync issues between the
        database and the search index - an object might already be
        deleted from the database while the index has not yet been
        updated. Our current solution is just to ignore and skip the
        result, so a result page might end up not actually containing
        less items than it's supposed to.

        ``index`` is normally the 0-based index to be used with
        ``get_hit``, but can also be an already retrieved
        ``SearchResult`` object.

        # TODO: get all content_object of same type instances at once
        """

        if isinstance(index, xappy.searchconnection.SearchResult):
            result = index
        else:
            result = self._results.get_hit(index)

        object_id, content_type_id = result.id.split('-')
        # 1) query content type
        try:
            content_type = ContentType.objects.get(pk=content_type_id)
        except ContentType.DoesNotExist:
            return False
        else:
            # 2) query model instance
            try:
                content_object = content_type.get_object_for_this_type(pk=object_id)
            except content_type.model_class().DoesNotExist:
                return False
            else:
                return XapianResult(result, content_object)

    def __iter__(self):
        for result in self._results:
            yield self._mkresult(result)

    def __getitem__(self, key):
        """Allow direct access to search results, even slice based.

        Pagination code requires might require this, but it is a bit
        tricky since we have only the explicitly requested results
        available in a 0-indexed fashion, but the calls to this function
        will use the "real" index. For example, results 51-60 are
        accessible for us via index 0-9, but requested using 50-59).
        """
        if isinstance(key, int):
            return self._mkresult(index-self.offset)
        elif isinstance(key, slice):
            result = []
            for i in range(*key.indices(self.count)):
                try:
                    result.append(self._mkresult(i-self.offset))
                except IndexError:
                    pass
            return result
        else:
            raise KeyError("key must be an int or slice")

    def __len__(self):
        """Return the exact number of items, or as closely as possible.

        See also ``count()``.
        """
        return self._results.matches_estimated

    @property
    def count(self):
        """Return the number of items suggested for display.

        See also ``__len__``.
        """
        return self._results.matches_human_readable_estimate

    @property
    def count_is_estimated(self):
        return not self._results.estimate_is_exact

    @property
    def xappy_results(self):
        """Return the lower-level Xappy result object.

        For access to advanced features.
        """
        return self._results


    @property
    def spell_suggestion(self):
        """Ask xappy for spelling suggestions on the query used.

        See also ``spell_suggestion_html``.
        """
        if not hasattr(self, '_spell_suggestion'):
            query_utf8 = self.query.encode('utf8')
            suggested_query = self._results._conn.spell_correct(query_utf8)
            self._spell_suggestion = (suggested_query != query_utf8 and
                                        [suggested_query.decode('utf-8')] or
                                        [None])[0]
        return self._spell_suggestion

    @property
    def spell_suggestion_html(self, format='<strong><em>%s</em></strong>'):
        """An html version of the spell suggestion string that
        has the changed terms highlighted.
        """
        if self.spell_suggestion:
            query_words = self.query.split(' ')
            fixed_words = self.spell_suggestion.split(' ')
            with_changed = []
            for word1, word2 in zip(query_words, fixed_words):
                if word1 != word2:
                    fstr = format
                else:
                    fstr = '%s'
                with_changed.append(fstr % word2)
            return mark_safe(' '.join(with_changed))
        return None


class XapianResult(object):
    """Simple wrapper around a Xappy ``SearchResult`` object.

    Provides certain functions we'd like to use in templates.
    """

    def __init__(self, result, content_object):
        self._result = result
        self.content_object = content_object

    def __getattr__(self, name):
        try:
            return getattr(self._result, name)
        except AttributeError:
            try:
                return self._result.data[name][0]
            except KeyError:
                return ''

    @template_callable
    def highlighted(self, field):
        return mark_safe(self._result.highlight(field)[0])

    @template_callable
    def summarised(self, field):
        return mark_safe(self._result.summarise(field, maxlen=180))

    @property
    def model(self):
        """Return the model name as a string.

        This is for use in templates:

            {% ifequal result.model "user" %}

        """
        return type(self.content_object).__name__.lower()