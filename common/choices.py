from django.db import models


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    UPI = "upi", "UPI"
    BANK = "bank", "Bank Transfer"
    CHEQUE = "cheque", "Cheque"
    CARD = "card", "Card"
    OTHER = "other", "Other"


class FuelType(models.TextChoices):
    DIESEL = "diesel", "Diesel"
    PETROL = "petrol", "Petrol"
    CNG = "cng", "CNG"
    ELECTRIC = "electric", "Electric"
    HYBRID = "hybrid", "Hybrid"
    OTHER = "other", "Other"
