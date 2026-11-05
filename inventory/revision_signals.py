"""Monotonic change marker, including a change followed by its reversal."""
from django.db.models.signals import post_save,post_delete
from django.db.models import F
from django.dispatch import receiver
from .models import Stock,Allocation,ItemRevision

@receiver(post_save,sender=Stock)
@receiver(post_delete,sender=Stock)
def stock_changed(sender,instance,raw=False,**kwargs):
    if raw:return
    marker,_=ItemRevision.objects.get_or_create(item_id=instance.item_id)
    ItemRevision.objects.filter(pk=marker.pk).update(revision=F('revision')+1)

@receiver(post_save,sender=Allocation)
@receiver(post_delete,sender=Allocation)
def allocation_changed(sender,instance,raw=False,**kwargs):
    if not raw and Stock.objects.filter(pk=instance.stock_id).exists():
        stock_changed(Stock,instance.stock)
