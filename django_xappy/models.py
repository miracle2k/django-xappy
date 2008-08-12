import datetime
from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.db.models import signals


class ChangeManager(models.Manager):
    def get_query_set(self):
        # We are trying to apply change changes in the order they
        # occurred: ``timestamp`` is the "correct" metric, especially
        # since an already changed object may be updated again, thus
        # modifying the change timestamp. However, it lacks precision,
        # so we additionally use ``id``.
        # Ultimately, the order shouldn't be all that important anyway.
        return super(ChangeManager, self).get_query_set().\
            order_by('timestamp', 'id')

    def ordered(self):
        """Allow explicit request of ordered queryset, semantics only.
        """
        return self.get_query_set()

    def before(self, dt):
        """Return changes before a certain datetime.
        """
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

    class Kind:
        add = 1
        update = 2
        delete = 3

    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    kind = models.IntegerField(choices=((Kind.add, 'add'),
                                        (Kind.update, 'update'),
                                        (Kind.delete, 'delete')))
    timestamp = models.DateTimeField(default=datetime.datetime.now)

    objects = ChangeManager()

    class Meta:
        unique_together = ('content_type', 'object_id')
        db_table = 'django_search_changes'

    @property
    def content_object(self):
        return self.content_type.get_object_for_this_type(pk=self.object_id)

    @property
    def model(self):
        return self.content_type.model_class()


# List of all models which's changes are logged. You're not supposed to
# interact with this code yourself - it is used to keep a global registry
# across multiple indexes. Register your models with your index.
_LOGGED_MODELS = []

def log_model(model):
    if not model in _LOGGED_MODELS:
        _LOGGED_MODELS.append(model)

def _handle_save(sender, instance, created, raw, **kwargs):
    if type(instance) in _LOGGED_MODELS:
        if created:
            Change.objects.log_add(instance)
        else:
            Change.objects.log_update(instance)

def _handle_delete(sender, instance, **kwargs):
    if type(instance) in _LOGGED_MODELS:
        Change.objects.log_delete(instance)

signals.post_save.connect(_handle_save)
signals.post_delete.connect(_handle_delete)