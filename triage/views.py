from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.views import role_required

from .engine import complete_session
from .forms import ContextForm, SymptomsForm
from .models import TriageSession

NAV = {"active_nav": "triage"}


def _own_session(request, pk):
    """A session that belongs to the logged-in patient (404 otherwise)."""
    return get_object_or_404(
        TriageSession.objects.select_related("result"), pk=pk, patient=request.user
    )


def _redirect_if_completed(session):
    if session.is_completed:
        return redirect("triage:result", pk=session.pk)
    return None


@role_required("patient")
def home(request):
    sessions = request.user.triage_sessions.select_related("result")
    context = {
        **NAV,
        "in_progress": sessions.filter(status=TriageSession.Status.IN_PROGRESS).first(),
        "history": sessions.filter(status=TriageSession.Status.COMPLETED),
    }
    return render(request, "triage/home.html", context)


@role_required("patient")
def start(request):
    form = ContextForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        session = form.save(commit=False)
        session.patient = request.user
        session.age = TriageSession.age_from_birth_date(request.user.patient_profile.date_of_birth)
        session.save()
        return redirect("triage:symptoms", pk=session.pk)
    return render(request, "triage/context.html", {**NAV, "form": form, "step": 1})


@role_required("patient")
def edit_context(request, pk):
    session = _own_session(request, pk)
    if done := _redirect_if_completed(session):
        return done
    form = ContextForm(request.POST if request.method == "POST" else None, instance=session)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("triage:symptoms", pk=session.pk)
    return render(request, "triage/context.html", {**NAV, "form": form, "session": session, "step": 1})


@role_required("patient")
def symptoms(request, pk):
    session = _own_session(request, pk)
    if done := _redirect_if_completed(session):
        return done
    form = SymptomsForm(request.POST if request.method == "POST" else None, session=session)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("triage:review", pk=session.pk)
    return render(request, "triage/symptoms.html", {**NAV, "form": form, "session": session, "step": 2})


@role_required("patient")
def review(request, pk):
    session = _own_session(request, pk)
    if done := _redirect_if_completed(session):
        return done
    entries = session.symptoms.all()
    if not entries:
        return redirect("triage:symptoms", pk=session.pk)
    if request.method == "POST":
        complete_session(session)
        return redirect("triage:result", pk=session.pk)
    return render(request, "triage/review.html", {**NAV, "session": session, "entries": entries, "step": 3})


@role_required("patient")
def result(request, pk):
    session = _own_session(request, pk)
    if not session.is_completed:
        return redirect("triage:review", pk=session.pk)
    context = {
        **NAV,
        "session": session,
        "result": session.result,
        "entries": session.symptoms.all(),
        "red_flags": session.result.red_flags.all(),
        "step": 4,
    }
    return render(request, "triage/result.html", context)


@role_required("patient")
@require_POST
def cancel(request, pk):
    session = _own_session(request, pk)
    if not session.is_completed:
        session.delete()
        messages.info(request, "L'évaluation en cours a été abandonnée.")
    return redirect("triage:home")
