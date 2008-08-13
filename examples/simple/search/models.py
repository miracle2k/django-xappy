from os import path
import django_xappy
from django_xappy import action, FieldActions

from django.contrib.auth.models import User
from main.models import Choice, Poll


class Index(django_xappy.Index):
    """Search index for the Choice, Poll and User models.
    """

    location = path.join(path.dirname(__file__), '..', 'index')

    class Data:
        # A user's name.
        @action(FieldActions.INDEX_EXACT)
        def name(self):
            if self == User:
                return self.content_object.username

        # A poll's question.
        @action(FieldActions.INDEX_FREETEXT, language='en', spell=True)
        @action(FieldActions.SORTABLE)
        def question(self):
            if self == Poll:
                return self.content_object.question

        # A choice's answer.
        @action(FieldActions.INDEX_FREETEXT, language='en', spell=True)
        def choice(self):
            if self == Choice:
                return self.content_object.choice

        # A user's join date or poll's publication date.
        @action(FieldActions.SORTABLE, type="date")
        def date(self):
            if self == User:
                return self.content_object.date_joined
            elif self == Poll:
                return self.content_object.pub_date

        # A choice's vote count.
        @action(FieldActions.SORTABLE, type="float")
        #@action(FieldActions.INDEX_EXACT)
        def votes(self):
            if self == Choice:
                return self.content_object.votes


Index.register(Choice)
Index.register(Poll)
Index.register(User)