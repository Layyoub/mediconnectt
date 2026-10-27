from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class FollowUpPlan(models.Model):
    appointment = models.OneToOneField(
        "appointments.Appointment", on_delete=models.CASCADE, related_name="follow_up_plan", verbose_name="rendez-vous"
    )
    title = models.CharField("titre du suivi", max_length=150)
    instructions = models.TextField("instructions générales", blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "plan de suivi"
        verbose_name_plural = "plans de suivi"

    def __str__(self):
        return self.title

    @property
    def progress(self):
        total = self.tasks.count()
        done = self.tasks.filter(status=FollowUpTask.Status.DONE).count()
        return {"total": total, "done": done, "percent": round(done * 100 / total) if total else 0}


class FollowUpTask(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "À faire"
        IN_PROGRESS = "in_progress", "En cours"
        DONE = "done", "Terminé"

    plan = models.ForeignKey(FollowUpPlan, on_delete=models.CASCADE, related_name="tasks", verbose_name="plan")
    title = models.CharField("tâche", max_length=160)
    details = models.CharField("précisions", max_length=500, blank=True)
    due_date = models.DateField("à faire avant le", null=True, blank=True)
    status = models.CharField("avancement", max_length=12, choices=Status.choices, default=Status.TODO)
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        ordering = ["due_date", "id"]
        verbose_name = "tâche de suivi"
        verbose_name_plural = "tâches de suivi"

    def __str__(self):
        return self.title

    def clean(self):
        if self.due_date and self.due_date < timezone.localdate() and not self.pk:
            raise ValidationError({"due_date": "Choisissez aujourd'hui ou une date ultérieure."})
