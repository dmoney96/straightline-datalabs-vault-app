# straightline-datalabs-vault-app
Straightline Datalabs Vault App

## Purpose
The Straightline Datalabs Vault is a secure, open-source data ingestion and analysis platform designed to help journalists and researchers access, archive, and search large, sensitive public document sets.

## Core Features (in development)
- Secure file ingestion and OCR processing pipeline.
- Local + Cloudflare-secured environment (Hetzner).
- Indexed search with text extraction for large PDF and image archives.
- Researcher-friendly modular design (Python, Whoosh, Tesseract).

## Ethical Framework
- Committed to transparency, human rights, and data ethics.
- No collection of private or PII data; all sources are public.
- Built for accountability and accessibility — never for surveillance.

## Current Status
Alpha setup phase — environment and backend scaffolding live.  
Next phase: automated document fetch + OCR layer integration.

---

# Technical Overview
The Straightline DataLabs Vault is an open-source ingestion and search pipeline optimized for investigative reporting, archival research, and public-interest journalism.

It automatically:
- Retrieves documents from remote URLs
- Converts PDFs into images
- Runs OCR using Tesseract
- Extracts searchable text
- Builds a full-text index using Whoosh
- Provides both CLI and HTTP search interfaces

---

## Core Technical Features

### Document Fetching
```bash
python scripts/fetch.py <url>
