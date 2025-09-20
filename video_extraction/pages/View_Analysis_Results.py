import streamlit as st
import pandas as pd
from pathlib import Path

st.set_page_config(page_title="Content Understanding — Keyframes", layout="wide")
st.title("Content Understanding Demo")
st.write("Displays objects detected in video frames.")

# In a multipage app, this file lives in /pages, so the app root is parent of this file’s parent.
APP_ROOT = Path(__file__).resolve().parents[1]
default_frames_dir = APP_ROOT / "keyframes"

frames_dir_str = st.text_input(
    "Folder to monitor (expects phrase_keyframe_map.csv and extracted keyframe images):",
    value=str(default_frames_dir),
)
FRAMES_DIR = Path(frames_dir_str)
CSV_PATH = FRAMES_DIR / "phrase_keyframe_map.csv"

@st.cache_data
def load_mappings(path: Path):
    df = pd.read_csv(path)
    # Ensure the filename column exists and strip whitespace
    if "matched_filename" in df.columns:
        df["matched_filename"] = df["matched_filename"].astype(str).str.strip()
    return df

# Simple monitor behavior: check for the CSV and offer a manual refresh
if not CSV_PATH.exists():
    st.warning(f"Waiting for mapping file: `{CSV_PATH}`")
    if st.button("Refresh"):
        st.rerun()
    st.stop()

try:
    df = load_mappings(CSV_PATH)
except FileNotFoundError:
    st.error(f"Mapping file not found: {CSV_PATH}")
    st.stop()

search = st.text_input("Object Search: ", "")
if search:
    filtered = df[df["phrase_text"].str.contains(search, case=False, na=False)]
else:
    filtered = df

st.write(f"Showing {len(filtered)} of {len(df)} mappings")

for _, row in filtered.iterrows():
    cols = st.columns([1, 2])
    img_path = FRAMES_DIR / row.get("matched_filename", "")
    with cols[0]:
        if img_path.exists():
            # Use a fixed width to make images smaller on the page
            st.image(str(img_path), width=280, caption=row.get("matched_filename"))
        else:
            st.warning(f"Image not found: {img_path.name}")
    with cols[1]:
        st.subheader(row.get("phrase_text", ""))
        st.write("**Phrase index:**", row.get("phrase_idx", ""))
        st.write("**Start:**", row.get("start_tc", ""), " — **End:**", row.get("end_tc", ""))
        st.write("**Matched keyframe timecode:**", row.get("matched_keyframe_tc", ""))
        st.markdown("---")

st.caption("Tip: Click **Refresh** in your browser or hit **R** to re-run after new results are written.")
