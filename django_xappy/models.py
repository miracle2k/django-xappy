import datetime
from django.db import models
from django.contrib.contenttypes.models import ContentType
from pyutils.xtypes import ValueEnum
from djutils import enum_as_choices


# List of models supporting the "Searchable" mixin; This list is built the
# first time it is needed (``Searchable.get_models``), and then cached for
# lifetime of the application.
SEARCHABLE_MODELS = []

class Searchable(models.Model):
    """Mixin that adds search functionality to a model.

    It will take care of logging changes to this model so the search
    index can be updated appropriately. Note that you will have to specify
    which of a model's fields to index (and how) in the ``IndexData`` class.
    """

    def save(self):
        is_new = self.pk is None
        super(Searchable, self).save()
        if is_new: Change.objects.log_add(self)
        else: Change.objects.log_update(self)

    def delete(self):
        Change.objects.log_delete(self)
        super(Searchable, self).delete()

    @staticmethod
    def get_models():
        """Find models using this mixin."""
        global SEARCHABLE_MODELS
        if not SEARCHABLE_MODELS:
            from django.db.models import get_apps, get_models
            SEARCHABLE_MODELS = []
            for app in get_apps():
                for model in get_models(app):
                    if issubclass(model, Searchable):
                        SEARCHABLE_MODELS.append(model)
        return SEARCHABLE_MODELS

    class Meta:
        abstract = True


class ChangeManager(models.Manager):
    def get_query_set(self):
        # While ``id`` could possible be enough, ``timestamp`` is more correct,
        # especially since a (unique) content type / object id combo can only
        # exist once and might be updated multiple times until the change is
        # applied to the search index - thus the ``id`` and ``timestamp``
        # orders may differ considerably. But ``timestamp`` is lacking enough
        # precision to be unique - so we combine both. Ultimately, the order
        # shouldn't even make much difference when updating the index, anyway.
        return super(ChangeManager, self).get_query_set().\
            order_by('timestamp', 'id')

    def ordered(self):
        """Allow explicit request of ordered queryset, semantics only."""
        return self.get_query_set()

    def before(self, dt):
        """Return changes before a certain datetime."""
        return self.get_query_set().filter(timestamp__lt=dt)

    def log(self, kind, instance):
        change, created = self.get_or_create(
            content_type=ContentType.objects.get_for_model(instance),
            object_id=instance.pk,
            defaults={'kind': kind})
        if not created:
            change.kind = kind
            change.timestamp = datetime.datetime.now()
            change.save()
    def log_delete(self, instance):
        self.log(Change.Kind.delete, instance)
    def log_add(self, instance):
        self.log(Change.Kind.add, instance)
    def log_update(self, instance):
        self.log(Change.Kind.update, instance)


class Change(models.Model):
    """Logs changes to all models that are searchable.

    A daemon/script/some kind of facility to update the search index
    can use this.
    """
    class Kind(ValueEnum):
        add = 1
        update = 2
        delete = 3
    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    kind = models.IntegerField(choices=enum_as_choices(Kind))
    timestamp = models.DateTimeField(default=datetime.datetime.now)

    objects = ChangeManager()

    class Meta:
        unique_together = ('content_type', 'object_id')
        db_table = 'search_change'

    @property
    def content_object(self):
        return self.content_type.get_object_for_this_type(pk=self.object_id)

    @property
    def model(self):
        return self.content_type.model_class()