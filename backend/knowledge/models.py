from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class KnowledgeSource(models.Model):
    """Explicitly selected source; existence does not imply publication or approval."""

    memory = models.OneToOneField(
        'workspace.ProjectMemory', null=True, blank=True,
        on_delete=models.CASCADE, related_name='knowledge_source',
    )
    documentation = models.OneToOneField(
        'workspace.ProjectDocumentation', null=True, blank=True,
        on_delete=models.CASCADE, related_name='knowledge_source',
    )
    decision = models.OneToOneField(
        'workspace.Decision', null=True, blank=True,
        on_delete=models.CASCADE, related_name='knowledge_source',
    )
    title = models.CharField(max_length=200)
    enabled = models.BooleanField(default=False)
    source_digest = models.CharField(max_length=64)
    source_updated_at = models.DateTimeField()
    indexed_at = models.DateTimeField(auto_now=True)

    @property
    def project_id(self):
        source = self.memory or self.documentation or self.decision
        return source.project_id

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(memory__isnull=False, documentation__isnull=True, decision__isnull=True)
                           | Q(memory__isnull=True, documentation__isnull=False, decision__isnull=True)
                           | Q(memory__isnull=True, documentation__isnull=True, decision__isnull=False)),
                name='knowledge_exactly_one_origin',
            ),
        ]

    def __str__(self):
        return self.title


class KnowledgeChunk(models.Model):
    source = models.ForeignKey(
        KnowledgeSource, on_delete=models.CASCADE, related_name='chunks',
    )
    position = models.PositiveIntegerField()
    text = models.TextField()
    digest = models.CharField(max_length=64)

    class Meta:
        ordering = ['source_id', 'position']
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'position'], name='knowledge_chunk_position',
            ),
            models.CheckConstraint(
                condition=~Q(text=''), name='knowledge_chunk_not_empty',
            ),
        ]

    def clean(self):
        super().clean()
        if not self.text.strip():
            raise ValidationError({'text': 'يجب أن يحتوي المقطع على نص.'})
