{#- The website pitch (spec v2 §8). Plain text. Echo fills in the greeting and opener; the footer
    with the legal parts is added by code for the recipient's country. -#}
{{ greeting }}

{{ opener }}

I couldn't find a website for {{ business_name }}, so I made one to show you what it could look like:
{{ preview_url }}

It's a real working page with your details on it, it works on phones, and it's hidden from Google until you decide what you'd like to do.{% if screenshot %} I've attached a screenshot of it on a phone.{% endif %}


{% if pricing %}If you'd like to keep it, it's {{ pricing }}, and I'll set it up on your own web address.{% else %}If you'd like to keep it, I can set it up on your own web address.{% endif %} Would you like me to send over the details?

{{ sender_name }}
{{ office_name }}
