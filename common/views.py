from django.db.models import ProtectedError, Sum
from rest_framework import status, viewsets
from rest_framework.response import Response

from accounts.permissions import ModulePermission


class BaseViewSet(viewsets.ModelViewSet):
    """
    Shared behaviour for every module:
      * role-based access through `module`
      * friendly message when a record can't be deleted because others depend on it
      * `totals` for the filtered list (e.g. total income for the selected month)
    """

    module = None
    sum_fields = ()
    protected_message = "This record is used elsewhere and can't be deleted."
    permission_classes = [ModulePermission]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        totals = {}
        for field in self.sum_fields:
            totals[field] = queryset.aggregate(total=Sum(field))["total"] or 0
        page = self.paginate_queryset(queryset)
        if page is not None:
            response = self.get_paginated_response(self.get_serializer(page, many=True).data)
        else:
            response = Response({"results": self.get_serializer(queryset, many=True).data})
        response.data["totals"] = totals
        return response

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        blocked = self.check_can_delete(instance)
        if blocked:
            return Response({"detail": blocked}, status=status.HTTP_400_BAD_REQUEST)
        try:
            instance.delete()
        except ProtectedError:
            return Response({"detail": self.protected_message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def check_can_delete(self, instance):
        """Return a message to block deletion, or None."""
        return None
