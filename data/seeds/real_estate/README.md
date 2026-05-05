# Real Estate Seed Data

## spell_corrections.csv

**Source:** Manual curation  
**License:** Internal  
**Refresh:** As needed — add rows when new misspellings are observed  
**Format:** `wrong,right,source,confidence`  

Common misspellings observed in real estate listing data (Toronto/GTA focus).
Load via: `python scripts/init_data.py --domain real_estate --only spell_corrections`

## FSA → Municipality (Wikipedia)

Loaded automatically by `WikipediaFSASeeder` from Wikipedia FSA redirect pages.  
**License:** CC BY-SA (Wikipedia)  
**Refresh:** Monthly recommended  
**Rate limit:** 0.5s between requests  

## StatsCan Shapefile (optional)

Census subdivision boundaries from Statistics Canada.  
**License:** Statistics Canada Open License  
**Refresh:** Post-census (every 5 years)  
**Download:** https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/  
**Enabled:** false by default (requires manual file download)  
