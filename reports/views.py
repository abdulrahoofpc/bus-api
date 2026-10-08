from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import access_for, can_see_finance
from common.dates import resolve_period
from common.params import check_query

from . import registry, services
from .exports import to_excel, to_pdf


def _allowed(user, module):
    return access_for(user).get(module) in ("r", "rw")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard(request):
    if not _allowed(request.user, "dashboard"):     # drivers have no dashboard access
        return Response({"detail": "Your role doesn't have access to the dashboard."}, status=403)
    return Response(services.dashboard(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def monthly_finance(request):
    if not can_see_finance(request.user):
        return Response({"detail": "Your role doesn't have access to finance figures."}, status=403)
    check_query(request.query_params)
    start, end, label = resolve_period(request.query_params)
    return Response(services.monthly_finance(start, end, label, request.query_params))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def report_list(request):
    if not _allowed(request.user, "reports"):
        return Response({"detail": "Your role doesn't have access to reports."}, status=403)
    return Response(registry.catalogue())


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def report_run(request, key):
    if not _allowed(request.user, "reports"):
        return Response({"detail": "Your role doesn't have access to reports."}, status=403)
    if key not in registry.REPORTS:
        return Response({"detail": "Report not found."}, status=404)
    check_query(request.query_params)
    export = request.query_params.get("export")
    if export not in (None, "", "pdf", "xlsx"):
        return Response({"export": ["Use pdf or xlsx."]}, status=400)
    data = registry.run(key, request.query_params)
    filename = f"{key}-{data['period'].replace(' ', '-').lower()}"
    if export == "xlsx":
        resp = HttpResponse(to_excel(data),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return resp
    if export == "pdf":
        resp = HttpResponse(to_pdf(data), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
        return resp
    return Response(data)
