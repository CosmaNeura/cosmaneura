{% block content %}

Hi {{ patient_name }},

Your provider is requesting a little information ahead of your appointment on {{ appointment_date }}.

Answering these questions takes just a few minutes and helps your provider prepare for your visit:

[Start your intake]({{ intake_url }})

If the button does not work, copy and paste this address into your browser:

{{ intake_url }}

{% endblock %}
