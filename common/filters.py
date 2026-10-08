import django_filters

from .dates import month_bounds


class PeriodVehicleFilter(django_filters.FilterSet):
    """
    Common filters: date range, month/year, vehicle and vehicle type.
    Subclasses set `date_field` and Meta.model.
    """

    date_field = "date"
    vehicle_field = "vehicle"

    date_from = django_filters.DateFilter(method="filter_date_from")
    date_to = django_filters.DateFilter(method="filter_date_to")
    month = django_filters.NumberFilter(method="filter_noop")
    year = django_filters.NumberFilter(method="filter_noop")
    vehicle_type = django_filters.NumberFilter(method="filter_vehicle_type")

    def filter_noop(self, queryset, name, value):
        return queryset

    def filter_date_from(self, queryset, name, value):
        return queryset.filter(**{f"{self.date_field}__gte": value})

    def filter_date_to(self, queryset, name, value):
        return queryset.filter(**{f"{self.date_field}__lte": value})

    def filter_vehicle_type(self, queryset, name, value):
        return queryset.filter(**{f"{self.vehicle_field}__vehicle_type_id": value})

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        data = self.form.cleaned_data
        year, month = data.get("year"), data.get("month")
        if year and month:
            start, end = month_bounds(int(year), int(month))
            queryset = queryset.filter(**{f"{self.date_field}__range": (start, end)})
        elif year:
            queryset = queryset.filter(**{f"{self.date_field}__year": int(year)})
        return queryset
