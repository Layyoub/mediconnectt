import mimetypes
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from appointments.views import verified_doctor_required
from appointments.models import Appointment
from core.models import get_role
from core.views import role_required
from pharmacheck import ai_agent as pharmacheck_ai
from pharmacheck import engine as pharmacheck_engine
from pharmacheck.views import valid_ai_source_check, validate_confirmed_resolutions
from teleconsult.models import ConsultationSummary, Notification
from teleconsult.services import notify
from triage.models import TriageSession

from . import access
from .forms import (
    DRUG_DATALIST_ID,
    DocumentForm,
    MedicalRecordForm,
    NoteForm,
    PrescriptionForm,
    PrescriptionItemFormSet,
    allergy_conflicts,
)
from .ai_analysis import start_analysis
from .models import (
    AccessLog, DocumentAnalysis, Drug, MedicalDocument, MedicalRecord, Prescription, PrescriptionCheck,
    PatientInstructions,
)

User = get_user_model()
Action = AccessLog.Action


def _layout(request, role, active_nav):
    ctx = {
        "role": role,
        "active_nav": active_nav,
        "layout": "core/patient/_layout.html" if role == "patient" else "core/doctor/_layout.html",
    }
    if role == "doctor":
        ctx["profile"] = request.user.doctor_profile
    return ctx


def _record_url(record, role, tab=None):
    if role == "patient":
        url = reverse("records:my_record")
    else:
        url = reverse("records:patient_record", args=[record.patient_id])
    return f"{url}?onglet={tab}" if tab else url


# --------------------------------------------------------------------------
# The record page (shared by the patient and the doctors who follow them)
# --------------------------------------------------------------------------

TABS = [
    ("apercu", "Aperçu", "grid-1x2"),
    ("notes", "Notes", "journal-text"),
    ("ordonnances", "Ordonnances", "capsule"),
    ("documents", "Documents", "folder2"),
    ("acces", "Accès au dossier", "shield-lock"),
]


def _record_page(request, record, role):
    keys = [key for key, _, _ in TABS if role == "patient" or key != "acces"]
    tab = request.GET.get("onglet", "apercu")
    if tab not in keys:
        tab = "apercu"
    if role == "doctor":
        access.log(request, record, Action.VIEW)

    patient = record.patient
    notes = record.notes.select_related("author", "appointment")
    if role == "patient":
        notes = notes.filter(visible_to_patient=True)
    prescriptions = record.prescriptions.select_related("doctor").prefetch_related("items__drug")
    documents = record.documents.select_related("uploaded_by")
    counts = {"notes": notes.count(), "ordonnances": prescriptions.count(), "documents": documents.count()}
    tabs = [
        {"key": key, "label": label, "icon": icon, "count": counts.get(key)}
        for key, label, icon in TABS if key in keys
    ]
    context = {
        **_layout(request, role, "records" if role == "patient" else "patients"),
        "record": record,
        "patient": patient,
        "patient_age": TriageSession.age_from_birth_date(patient.patient_profile.date_of_birth),
        "tabs": tabs,
        "tab": tab,
        "notes": notes,
        "prescriptions": prescriptions,
        "documents": documents,
        "followup_appointment": Appointment.objects.filter(
            doctor=request.user, patient=patient,
            status__in=[Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED],
            follow_up_plan__isnull=True,
        ).order_by("-start").first() if role == "doctor" else None,
        "consultations": ConsultationSummary.objects.filter(room__appointment__patient=patient)
        .select_related("room__appointment__doctor", "room__appointment__specialty"),
    }
    if tab == "acces":
        context["logs"] = record.access_logs.select_related("user__doctor_profile")[:100]
    return render(request, "records/record.html", context)


def _save_record_form(request, record, role):
    form = MedicalRecordForm(request.POST or None, instance=record)
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.updated_by = request.user
        record.save()
        changed = ", ".join(str(form.fields[f].label) for f in form.changed_data)
        access.log(request, record, Action.UPDATE, changed)
        if role == "doctor":
            notify(
                record.patient, Notification.Kind.RECORD, "Votre dossier médical a été mis à jour",
                f"Dr {request.user.get_full_name()} a mis à jour vos informations médicales.",
                reverse("records:my_record"),
            )
        messages.success(request, "Informations médicales enregistrées.")
        return redirect(_record_url(record, role))
    context = {**_layout(request, role, "records" if role == "patient" else "patients"),
               "form": form, "record": record, "back": _record_url(record, role)}
    return render(request, "records/record_form.html", context)


def _upload_document(request, record, role):
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        document = form.save(commit=False)
        document.record = record
        document.uploaded_by = request.user
        upload = form.cleaned_data["file"]
        document.original_name = upload.name[:200]
        document.size = upload.size
        document.save()
        access.log(request, record, Action.DOCUMENT_ADD, document.title)
        if role == "doctor":
            notify(
                record.patient, Notification.Kind.RECORD, "Nouveau document dans votre dossier",
                f"Dr {request.user.get_full_name()} a ajouté « {document.title} ».",
                f"{reverse('records:my_record')}?onglet=documents",
            )
        messages.success(request, "Document ajouté au dossier.")
        return redirect(_record_url(record, role, "documents"))
    context = {**_layout(request, role, "records" if role == "patient" else "patients"),
               "form": form, "record": record, "back": _record_url(record, role, "documents")}
    return render(request, "records/document_form.html", context)


# --------------------------------------------------------------------------
# Patient
# --------------------------------------------------------------------------

@role_required("patient")
def my_record(request):
    return _record_page(request, MedicalRecord.for_patient(request.user), "patient")


@role_required("patient")
def edit_my_record(request):
    return _save_record_form(request, MedicalRecord.for_patient(request.user), "patient")


@role_required("patient")
def add_my_document(request):
    return _upload_document(request, MedicalRecord.for_patient(request.user), "patient")


# --------------------------------------------------------------------------
# Doctor
# --------------------------------------------------------------------------

def _followed_record(request, patient_id):
    patient = get_object_or_404(User.objects.select_related("patient_profile"), pk=patient_id,
                                patient_profile__isnull=False)
    if not access.doctor_follows(request.user, patient):
        raise Http404
    return MedicalRecord.for_patient(patient)


@verified_doctor_required
def patient_list(request):
    query = request.GET.get("q", "").strip()
    patients = (
        User.objects.filter(
            patient_profile__isnull=False,
            patient_appointments__doctor=request.user,
            patient_appointments__status__in=access.CARE_STATUSES,
        )
        .annotate(last_visit=Max("patient_appointments__start"), visits=Count("patient_appointments"))
        .select_related("patient_profile", "medical_record")
        .order_by("last_name", "first_name")
    )
    if query:
        patients = patients.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query))
    patients = list(patients)
    for p in patients:
        p.age = TriageSession.age_from_birth_date(p.patient_profile.date_of_birth)
    context = {**_layout(request, "doctor", "patients"), "patients": patients, "query": query}
    return render(request, "records/patient_list.html", context)


@verified_doctor_required
def patient_record(request, patient_id):
    return _record_page(request, _followed_record(request, patient_id), "doctor")


@verified_doctor_required
def doctor_edit_record(request, patient_id):
    return _save_record_form(request, _followed_record(request, patient_id), "doctor")


@verified_doctor_required
def doctor_add_document(request, patient_id):
    return _upload_document(request, _followed_record(request, patient_id), "doctor")


@verified_doctor_required
def add_note(request, patient_id):
    record = _followed_record(request, patient_id)
    form = NoteForm(request.POST or None, doctor=request.user, patient=record.patient,
                    initial={"appointment": request.GET.get("rdv")})
    if request.method == "POST" and form.is_valid():
        note = form.save(commit=False)
        note.record = record
        note.author = request.user
        note.save()
        access.log(request, record, Action.NOTE, note.title)
        if note.visible_to_patient:
            notify(
                record.patient, Notification.Kind.RECORD, "Nouvelle note dans votre dossier",
                f"Dr {request.user.get_full_name()} : {note.title}",
                f"{reverse('records:my_record')}?onglet=notes",
            )
        messages.success(request, "Note ajoutée au dossier.")
        return redirect(_record_url(record, "doctor", "notes"))
    context = {**_layout(request, "doctor", "patients"), "form": form, "record": record,
               "back": _record_url(record, "doctor", "notes")}
    return render(request, "records/note_form.html", context)


@verified_doctor_required
def new_prescription(request, patient_id):
    record = _followed_record(request, patient_id)
    post = request.POST if request.method == "POST" else None
    form = PrescriptionForm(post, doctor=request.user, patient=record.patient,
                            initial={"appointment": request.GET.get("rdv")})
    formset = PrescriptionItemFormSet(post, instance=Prescription(), prefix="items")
    conflicts = []
    override_reason_error = ""
    if post is not None and form.is_valid() and formset.is_valid():
        draft_items = [
            item_form.save(commit=False)
            for item_form in formset.forms
            if item_form.cleaned_data.get("drug_name")
        ]
        names = [item.drug_name for item in draft_items]
        conflicts = allergy_conflicts(record, names)
        confirmation_error = False
        try:
            submitted_resolutions = json.loads(post.get("pharmacheck_confirmed_resolutions", "[]"))
            confirmed_resolutions = validate_confirmed_resolutions(
                record, request.user, submitted_resolutions,
                [
                    {
                        "drug_name": item.drug_name,
                        "dosage": item.dosage,
                        "frequency": item.frequency,
                        "duration": item.duration,
                    }
                    for item in draft_items
                ],
            )
        except ValueError as exc:
            confirmation_error = True
            confirmed_resolutions = []
            form.add_error(None, str(exc))
        checked_items = [
            {
                "drug_name": item.drug_name,
                "dosage": item.dosage,
                "frequency": item.frequency,
                "duration": item.duration,
            }
            for item in draft_items
        ]
        findings = pharmacheck_engine.check(record, draft_items, confirmed_resolutions)
        override_reason = post.get("override_reason", "").strip()
        ai_source = valid_ai_source_check(
            record, request.user, post.get("pharmacheck_ai_check_id"), checked_items
        )
        ai_opinion = json.loads(json.dumps(ai_source.ai_opinion)) if ai_source else {}
        rule_level = pharmacheck_engine.max_severity(findings)
        final_level = max(
            [rule_level, ai_opinion.get("final_level", "info")],
            key=pharmacheck_engine.SEVERITY_ORDER.index,
        )
        safety_check = PrescriptionCheck.objects.create(
            record=record,
            doctor=request.user,
            findings=findings,
            checked_items=checked_items,
            confirmed_resolutions=confirmed_resolutions,
            max_severity=final_level,
            override_reason=override_reason,
            ai_opinion=ai_opinion,
            ai_status=(
                PrescriptionCheck.AIStatus.DONE if ai_source
                else PrescriptionCheck.AIStatus.IDLE
            ),
            ai_model=ai_source.ai_model if ai_source else "",
            ai_cache_key=ai_source.ai_cache_key if ai_source else "",
        )
        access.log(
            request, record, Action.PRESCRIPTION_CHECK, f"Vérification Pharma-Check {safety_check.pk}"
        )
        if confirmation_error:
            pass
        elif pharmacheck_engine.requires_override(findings) and len(override_reason) < 10:
            override_reason_error = "Pour enregistrer malgré une alerte majeure ou contre-indiquée, indiquez un motif d'au moins 10 caractères."
            form.add_error(None, override_reason_error)
        else:
            with transaction.atomic():
                prescription = form.save(commit=False)
                prescription.record = record
                prescription.doctor = request.user
                prescription.save()
                formset.instance = prescription
                formset.save()
                safety_check.prescription = prescription
                safety_check.save(update_fields=["prescription"])
            access.log(request, record, Action.PRESCRIPTION, prescription.code)
            pharmacheck_ai.start_patient_instructions(prescription)
            notify(
                record.patient, Notification.Kind.PRESCRIPTION, "Nouvelle ordonnance",
                f"Dr {request.user.get_full_name()} vous a prescrit : {prescription.title}.",
                reverse("records:prescription", args=[prescription.code]),
            )
            messages.success(request, "Ordonnance enregistrée. Le patient peut la consulter et l'imprimer.")
            return redirect("records:prescription", code=prescription.code)
    context = {
        **_layout(request, "doctor", "patients"),
        "form": form, "formset": formset, "record": record, "conflicts": conflicts,
        "override_reason_error": override_reason_error,
        "pharmacheck_ai_enabled": settings.PHARMACHECK_AI_ENABLED,
        "back": _record_url(record, "doctor", "ordonnances"),
        "suggestions": Drug.objects.filter(is_active=True),
        "datalist_id": DRUG_DATALIST_ID,
    }
    return render(request, "records/prescription_form.html", context)


# --------------------------------------------------------------------------
# Shared: prescriptions and documents
# --------------------------------------------------------------------------

@login_required
def prescription_list(request):
    role = get_role(request.user)
    if role == "patient":
        prescriptions = Prescription.objects.filter(record__patient=request.user)
        nav = "prescriptions"
    elif role == "doctor" and request.user.doctor_profile.is_verified:
        prescriptions = Prescription.objects.filter(doctor=request.user)
        nav = "prescriptions"
    else:
        return redirect("dashboard")
    prescriptions = prescriptions.select_related("doctor", "record__patient").prefetch_related("items__drug")
    context = {**_layout(request, role, nav), "prescriptions": prescriptions}
    return render(request, "records/prescription_list.html", context)


def _viewable(request, record):
    role = get_role(request.user)
    if role not in ("patient", "doctor") or not access.can_view(request.user, record):
        raise Http404
    return role


@login_required
def prescription_detail(request, code):
    prescription = get_object_or_404(
        Prescription.objects.select_related("record__patient__patient_profile", "doctor__doctor_profile", "appointment"),
        code=code,
    )
    record = prescription.record
    role = _viewable(request, record)
    if request.user != prescription.doctor:
        access.log(request, record, Action.PRESCRIPTION_VIEW, prescription.code)
    patient = record.patient
    pharmacheck_check = None
    patient_instructions = None
    if role == "doctor" and request.user == prescription.doctor:
        pharmacheck_check = prescription.safety_checks.filter(doctor=request.user).first()
        patient_instructions = PatientInstructions.objects.filter(prescription=prescription).first()
        if patient_instructions:
            pharmacheck_ai.mark_instructions_stale(patient_instructions)
            patient_instructions.refresh_from_db()
    elif role == "patient":
        patient_instructions = PatientInstructions.objects.filter(
            prescription=prescription, status=PatientInstructions.Status.APPROVED
        ).first()
        if patient_instructions:
            access.log(
                request, record, Action.RX_INSTRUCTIONS,
                f"Consignes validées pour {prescription.code}",
            )
    context = {
        **_layout(request, role, "prescriptions"),
        "prescription": prescription,
        "items": prescription.items.select_related("drug"),
        "patient": patient,
        "patient_age": TriageSession.age_from_birth_date(patient.patient_profile.date_of_birth),
        "record": record,
        "back": reverse("records:prescriptions") if role == "patient" else _record_url(record, role, "ordonnances"),
        "pharmacheck_check": pharmacheck_check,
        "patient_instructions": patient_instructions,
    }
    return render(request, "records/prescription_detail.html", context)


@verified_doctor_required
@require_POST
def update_patient_instructions(request, code):
    prescription = get_object_or_404(Prescription, code=code, doctor=request.user)
    instructions = get_object_or_404(PatientInstructions, prescription=prescription)
    if instructions.generation_status != PatientInstructions.GenerationStatus.DONE:
        messages.error(request, "Les consignes IA ne sont pas prêtes ; elles ne peuvent pas être publiées.")
        return redirect("records:prescription", code=prescription.code)
    text_fr = request.POST.get("text_fr", "").strip()
    text_ar = request.POST.get("text_ar", "").strip()
    if len(text_fr) > 10000 or len(text_ar) > 10000:
        messages.error(request, "Les consignes sont trop longues.")
        return redirect("records:prescription", code=prescription.code)
    instructions.text_fr = text_fr
    instructions.text_ar = text_ar
    action = request.POST.get("instruction_action")
    if action == "approve":
        if not text_fr or not text_ar:
            messages.error(request, "Les consignes doivent être complètes en français et en arabe avant validation.")
            return redirect("records:prescription", code=prescription.code)
        instructions.status = PatientInstructions.Status.APPROVED
        instructions.approved_by = request.user
        instructions.approved_at = timezone.now()
        message = "Consignes bilingues approuvées et visibles par le patient."
    elif action == "save":
        instructions.status = PatientInstructions.Status.DRAFT
        instructions.approved_by = None
        instructions.approved_at = None
        message = "Brouillon des consignes enregistré ; le patient ne le voit pas."
    else:
        messages.error(request, "Action de validation inconnue.")
        return redirect("records:prescription", code=prescription.code)
    instructions.save()
    access.log(request, prescription.record, Action.RX_INSTRUCTIONS, message)
    messages.success(request, message)
    return redirect("records:prescription", code=prescription.code)


@login_required
def document_file(request, pk):
    document = get_object_or_404(MedicalDocument.objects.select_related("record"), pk=pk)
    _viewable(request, document.record)
    if request.user.pk != document.uploaded_by_id:
        access.log(request, document.record, Action.DOCUMENT_VIEW, document.title)
    try:
        handle = document.file.open("rb")
    except FileNotFoundError:
        raise Http404
    content_type = mimetypes.guess_type(document.file.name)[0] or "application/octet-stream"
    filename = document.original_name or f"document.{document.extension.lower()}"
    return FileResponse(handle, content_type=content_type, filename=filename,
                        as_attachment=request.GET.get("telecharger") == "1")


@verified_doctor_required
def document_analysis(request, pk):
    """AI report on a patient's document, for the doctors who follow the patient."""
    document = get_object_or_404(
        MedicalDocument.objects.select_related("record__patient__patient_profile", "uploaded_by", "appointment"), pk=pk
    )
    record = document.record
    if not access.doctor_follows(request.user, record.patient):
        raise Http404
    if request.method == "POST":
        running = document.analyses.filter(status=DocumentAnalysis.Status.RUNNING)
        if not any(a.is_running for a in running):
            start_analysis(document, by=request.user)
            access.log(request, record, Action.DOCUMENT_ANALYSIS, document.title)
        return redirect("records:document_analysis", pk=document.pk)
    analysis = document.analyses.first()
    if analysis is None:
        analysis = start_analysis(document, by=request.user)
        access.log(request, record, Action.DOCUMENT_ANALYSIS, document.title)
    room = getattr(document.appointment, "room", None) if document.appointment else None
    context = {
        **_layout(request, "doctor", "patients"),
        "document": document,
        "analysis": analysis,
        "patient": record.patient,
        "patient_age": TriageSession.age_from_birth_date(record.patient.patient_profile.date_of_birth),
        "record": record,
        "room": room,
    }
    return render(request, "records/document_analysis.html", context)


def _analysis_json(document, analysis):
    if analysis is None:
        return {"status": "none"}
    status = "error" if analysis.is_stale else ("running" if analysis.is_running else analysis.status)
    return {
        "status": status,
        "document": document.title,
        "document_type": analysis.document_type,
        "exam_date": analysis.exam_date,
        "readable": analysis.readable,
        "summary": analysis.summary,
        "abnormal_findings": analysis.abnormal_findings,
        "points_to_check": analysis.points_to_check,
        "abnormal_values": analysis.abnormal_values,
        "values_count": len(analysis.values),
        "error": analysis.error,
        "report_url": reverse("records:document_analysis", args=[document.pk]),
    }


@verified_doctor_required
def document_analysis_api(request, pk):
    """Used by the "Lire avec l'IA" button in the consultation chat.
    GET: current state of the AI reading. POST: start it (if not already running or done)."""
    document = get_object_or_404(MedicalDocument.objects.select_related("record__patient"), pk=pk)
    record = document.record
    if not access.doctor_follows(request.user, record.patient):
        raise Http404
    analysis = document.analyses.first()
    if request.method == "POST":
        restart = request.POST.get("relancer") == "1"
        if analysis is None or analysis.is_stale or analysis.status == DocumentAnalysis.Status.ERROR or (
            restart and not analysis.is_running
        ):
            analysis = start_analysis(document, by=request.user)
            access.log(request, record, Action.DOCUMENT_ANALYSIS, document.title)
    return JsonResponse(_analysis_json(document, analysis))


@login_required
@require_POST
def document_delete(request, pk):
    document = get_object_or_404(MedicalDocument.objects.select_related("record"), pk=pk)
    role = _viewable(request, document.record)
    if request.user.pk != document.uploaded_by_id:
        raise Http404
    record = document.record
    title = document.title
    document.file.delete(save=False)
    document.delete()
    access.log(request, record, Action.DOCUMENT_DELETE, title)
    messages.success(request, "Document supprimé.")
    return redirect(_record_url(record, role, "documents"))
