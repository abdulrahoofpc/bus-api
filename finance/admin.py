from django.contrib import admin

from .models import DriverPayment, EMIInstallment, Expense, FuelEntry, Income, Loan, Maintenance

for model in (Income, Expense, FuelEntry, Maintenance, DriverPayment, Loan, EMIInstallment):
    admin.site.register(model)
