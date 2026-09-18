from django import forms

class ImportChannelsForm(forms.Form):
    urls = forms.CharField(
        label="",
        widget=forms.Textarea(attrs={'rows': 10, 'cols': 80})
    )

class BulkDeleteChannelsForm(forms.Form):
    urls = forms.CharField(
        label="",
        widget=forms.Textarea(attrs={'rows': 10, 'cols': 80})
    )