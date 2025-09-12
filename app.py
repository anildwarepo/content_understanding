import streamlit as st
import pandas as pd
from pathlib import Path

st.set_page_config(page_title="Content Understanding — Keyframes", layout="wide")
st.title("Content Understanding Demo")
st.write("Displays objects detected in video frames.")

BASE_DIR = Path(__file__).resolve().parent
FRAMES_DIR = BASE_DIR / "frames"
CSV_PATH = FRAMES_DIR / "phrase_keyframe_map.csv"

@st.cache_data
def load_mappings(path: Path):
    df = pd.read_csv(path)
    # Ensure the filename column exists and strip whitespace
    if "matched_filename" in df.columns:
        df["matched_filename"] = df["matched_filename"].astype(str).str.strip()
    return df

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
