import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from appointments.models import Appointment
from appointments.views import verified_doctor_required
from core.models import get_role
from core.views import role_required
from teleconsult.models import Notification
from teleconsult.services import notify
from .forms import FollowUpPlanForm, FollowUpTaskForm, TaskProgressForm
from .models import FollowUpPlan, FollowUpTask

logger = logging.getLogger(__name__)


def _doctor_plan(request, pk):
    plan = get_object_or_404(
        FollowUpPlan.objects.select_related("appointment__patient", "appointment__doctor"), pk=pk
    )
    if plan.appointment.doctor_id != request.user.pk:
        raise Http404
    return plan


def _patient_plan(request, pk):
    return get_object_or_404(
        FollowUpPlan.objects.select_related("appointment__patient", "appointment__doctor")
        .prefetch_related("tasks"), pk=pk, appointment__patient=request.user,
    )


def _detail(request, plan, role):
    return render(request, "followups/detail.html", {
        "plan": plan, "tasks": plan.tasks.all(), "role": role,
        "layout": "core/doctor/_layout.html" if role == "doctor" else "core/patient/_layout.html",
        "active_nav": "followups",
        "suggestions": request.session.get(f"followup_suggestions_{plan.pk}", []) if role == "doctor" else [],
        "ai_enabled": settings.FOLLOWUP_AI_ENABLED,
    })


@role_required("patient")
def patient_plans(request):
    plans = FollowUpPlan.objects.filter(appointment__patient=request.user).select_related(
        "appointment__doctor"
    ).prefetch_related("tasks")
    return render(request, "followups/list.html", {
        "plans": plans, "layout": "core/patient/_layout.html", "active_nav": "followups", "role": "patient",
    })


@verified_doctor_required
def doctor_plans(request):
    plans = FollowUpPlan.objects.filter(appointment__doctor=request.user).select_related(
        "appointment__patient"
    ).prefetch_related("tasks")
    appointments_ready = Appointment.objects.filter(
        doctor=request.user,
        status__in=[Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED],
        follow_up_plan__isnull=True,
    ).select_related("patient").order_by("-start")
    return render(request, "followups/list.html", {
        "plans": plans, "appointments_ready": appointments_ready,
        "layout": "core/doctor/_layout.html", "active_nav": "followups", "role": "doctor",
    })


@verified_doctor_required
def create_plan(request, patient_id, appointment_id):
    appointment = get_object_or_404(
        Appointment, pk=appointment_id, patient_id=patient_id, doctor=request.user,
        status__in=[Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED],
    )
    if hasattr(appointment, "follow_up_plan"):
        return redirect("followups:detail", pk=appointment.follow_up_plan.pk)
    form = FollowUpPlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        plan = form.save(commit=False)
        plan.appointment = appointment
        plan.save()
        notify(
            appointment.patient, Notification.Kind.RECORD, "Votre plan de suivi est prêt",
            f"Dr {request.user.get_full_name()} a créé « {plan.title} » après votre consultation.",
            reverse("followups:detail", args=[plan.pk]),
        )
        messages.success(request, "Plan de suivi créé. Ajoutez les tâches convenues avec le patient.")
        return redirect("followups:detail", pk=plan.pk)
    return render(request, "followups/plan_form.html", {
        "form": form, "appointment": appointment, "layout": "core/doctor/_layout.html", "active_nav": "patients",
        "back": f"/espace-medecin/patients/{patient_id}/",
    })


def plan_detail(request, pk):
    role = get_role(request.user) if request.user.is_authenticated else None
    if role == "patient":
        return _detail(request, _patient_plan(request, pk), role)
    if role == "doctor":
        if not request.user.doctor_profile.is_verified:
            raise Http404
        return _detail(request, _doctor_plan(request, pk), role)
    raise Http404


@verified_doctor_required
def edit_plan(request, pk):
    plan = _doctor_plan(request, pk)
    form = FollowUpPlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Plan de suivi modifié.")
        return redirect("followups:detail", pk=pk)
    return render(request, "followups/plan_form.html", {
        "form": form, "appointment": plan.appointment, "plan": plan,
        "layout": "core/doctor/_layout.html", "active_nav": "followups",
        "back": f"/suivi/{pk}/",
    })


@verified_doctor_required
@require_POST
def delete_plan(request, pk):
    plan = _doctor_plan(request, pk)
    patient_id = plan.appointment.patient_id
    plan.delete()
    messages.success(request, "Plan de suivi supprimé.")
    return redirect("records:patient_record", patient_id=patient_id)


def _task_form(request, pk, task_id=None):
    plan = _doctor_plan(request, pk)
    task = get_object_or_404(FollowUpTask, pk=task_id, plan=plan) if task_id else None
    form = FollowUpTaskForm(request.POST or None, instance=task)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.plan = plan
        saved.save()
        messages.success(request, "Tâche enregistrée.")
        return redirect("followups:detail", pk=pk)
    return render(request, "followups/task_form.html", {
        "form": form, "plan": plan, "layout": "core/doctor/_layout.html", "active_nav": "followups",
        "back": f"/suivi/{pk}/",
    })


@verified_doctor_required
def add_task(request, pk):
    return _task_form(request, pk)


@verified_doctor_required
def edit_task(request, pk, task_id):
    return _task_form(request, pk, task_id)


@verified_doctor_required
@require_POST
def delete_task(request, pk, task_id):
    plan = _doctor_plan(request, pk)
    get_object_or_404(FollowUpTask, pk=task_id, plan=plan).delete()
    messages.success(request, "Tâche supprimée.")
    return redirect("followups:detail", pk=pk)


@role_required("patient")
@require_POST
def update_progress(request, pk, task_id):
    plan = _patient_plan(request, pk)
    task = get_object_or_404(FollowUpTask, pk=task_id, plan=plan)
    form = TaskProgressForm(request.POST, instance=task)
    if form.is_valid():
        form.save()
        notify(
            plan.appointment.doctor, Notification.Kind.RECORD,
            f"{plan.appointment.patient.get_full_name()} a mis à jour son suivi",
            f"Avancement de « {task.title} » : {task.get_status_display()}.",
            reverse("followups:detail", args=[plan.pk]),
        )
        messages.success(request, "Votre avancement a été mis à jour.")
    else:
        messages.error(request, "Statut invalide.")
    return redirect("followups:detail", pk=pk)


@verified_doctor_required
@require_POST
def ai_suggestions(request, pk):
    plan = _doctor_plan(request, pk)
    if not settings.FOLLOWUP_AI_ENABLED:
        messages.info(request, "Les suggestions IA sont désactivées. Vous pouvez ajouter les tâches manuellement.")
        return redirect("followups:detail", pk=pk)
    try:
        from .ai import suggest_tasks
        suggestions = suggest_tasks(plan.appointment)
        today = timezone.localdate()
        serialized = [
            {"title": item.title, "details": item.details,
             "due_date": (today + timedelta(days=item.days_from_now)).isoformat()}
            for item in suggestions.tasks
        ]
        request.session[f"followup_suggestions_{pk}"] = serialized
    except Exception:
        logger.exception("AI follow-up suggestions failed for plan %s", pk)
        messages.error(request, "Impossible de générer les suggestions maintenant. Vous pouvez ajouter les tâches manuellement.")
    return redirect("followups:detail", pk=pk)


@verified_doctor_required
@require_POST
def add_suggestion(request, pk):
    plan = _doctor_plan(request, pk)
    index = request.POST.get("index", "")
    suggestions = request.session.get(f"followup_suggestions_{pk}", [])
    try:
        selected = int(index)
        if selected < 0 or selected >= len(suggestions):
            raise IndexError
        form = FollowUpTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.plan = plan
            task.save()
            suggestions.pop(selected)
            request.session[f"followup_suggestions_{pk}"] = suggestions
            messages.success(request, "Suggestion ajoutée au plan après validation.")
        else:
            messages.error(request, "Suggestion invalide. Vous pouvez créer la tâche manuellement.")
    except (ValueError, IndexError):
        messages.error(request, "Cette suggestion n'est plus disponible.")
    return redirect("followups:detail", pk=pk)
