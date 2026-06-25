-- EZPALExporter configuration
-- Directory where JSON files are written. EZPAL API reads from here.
-- Set EZPAL_DATA_DIR environment variable to override (recommended).
-- Fallback: writes to ./ezpal_live/ relative to the PalServer binary.
output_dir = os.getenv("EZPAL_DATA_DIR") or "./ezpal_live/"

-- How often to scan players and write JSON (seconds)
tick_interval = 5

-- How many pals to show in "recent pals" list (0 = all)
max_pals_output = 0
