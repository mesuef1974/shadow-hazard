import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from workspace.models import Project,ModelPolicy,SessionCharter,ModelJob
from .models import ReviewAssignment
from .review_assignment import assign_pending_reviews


class AutomaticReviewTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.project=Project.objects.create(name='test')
        p=ModelPolicy.objects.create(category='test',title='test')
        self.charters={k:SessionCharter.objects.create(key=k,name=k,project=self.project,policy=p,thread_id=str(uuid.uuid4())) for k in ['coordination','review','backend']}

    def job(self,key='backend',state='needs_review'):
        return ModelJob.objects.create(owner=self.owner,charter=self.charters[key],prompt='original',state=state,
            requested_model='gpt-6-luna',requested_thinking='low')

    def test_one_batch_and_one_link_per_original_without_recursion(self):
        jobs=[self.job() for _ in range(10)]
        self.job('review')
        first=assign_pending_reviews();second=assign_pending_reviews()
        self.assertEqual(first['created_batches'],1)
        self.assertEqual(second['created_batches'],0)
        self.assertEqual(ReviewAssignment.objects.count(),8)
        review=ReviewAssignment.objects.first().review_job
        self.assertEqual(review.charter.key,'review')
        self.assertEqual(ModelJob.objects.filter(charter=self.charters['review']).count(),2)
        review.state='needs_review';review.save()
        self.assertEqual(assign_pending_reviews()['created_batches'],0)
        self.assertFalse(ReviewAssignment.objects.filter(source_job=review).exists())
        review.state='completed';review.save()
        self.assertEqual(assign_pending_reviews()['created_batches'],0)
        from workspace.memory import document
        for link in ReviewAssignment.objects.all():
            document(self.project,'review',f'automatic-review:{link.source_job_id}','نتيجة مراجعة',{'verdict':'BLOCKED'})
        self.assertEqual(assign_pending_reviews()['created_batches'],1)
        self.assertEqual(ReviewAssignment.objects.count(),10)
        for job in jobs:
            job.refresh_from_db();self.assertEqual(job.state,'needs_review')

    def test_inactive_owner_and_disabled_destination_are_not_enqueued(self):
        self.job();self.owner.is_active=False;self.owner.save()
        self.assertEqual(assign_pending_reviews()['created_batches'],0)
        self.owner.is_active=True;self.owner.save()
        self.charters['review'].policy.enabled=False;self.charters['review'].policy.save()
        self.assertEqual(assign_pending_reviews()['created_batches'],0)

    def test_no_eligible_work_creates_no_model_job(self):
        self.job(state='completed');self.job('review')
        count=ModelJob.objects.count()
        self.assertEqual(assign_pending_reviews()['created_batches'],0)
        self.assertEqual(ModelJob.objects.count(),count)
