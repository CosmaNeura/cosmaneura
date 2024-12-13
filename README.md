# cosmaneura

This is the primary repo for cosmaneura.com.


[Generated from python-starter-template.](http://github.com/iloveitaly/python-starter-template)

# PHI Management

Here are the rules we use to manage PHI. You must follow these rules:

- Never store PHI on your machine.
- Prefer maintaining a manually curated, high-quality de-identified data set for all development and testing to reduce security overhead and ensure compliance.
- Utilize Azure’s [De-identification API](https://learn.microsoft.com/en-us/azure/healthcare-apis/deidentification/overview) to anonymize PHI, simplifying security requirements by removing PHI classification.
- Restrict data access to authorized personnel, encrypt data both in transit and at rest using strong algorithms, and continuously monitor data usage with regular security audits.
