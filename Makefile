PYTHONPATH ?= src
PYTHON ?= python3

.PHONY: newsletter newsletter-email email-latest email-test

newsletter:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m newsletter_diaria.main --ai-mode required

newsletter-email:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m newsletter_diaria.main --send-email --ai-mode required

email-latest:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m newsletter_diaria.main --send-latest

email-test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m newsletter_diaria.main --test-email
