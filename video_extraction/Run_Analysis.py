import streamlit as st
from pathlib import Path
from video_analysis import run_video_analysis
from extract_keyframes import run_frame_extraction

st.set_page_config(page_title="Content Understanding — Upload", layout="wide")
st.title("Upload & Analyze a Video")

UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

st.write("1) Upload a video file, 2) I’ll save it locally, 3) I’ll call `run_video_analysis`.")

uploaded = st.file_uploader("Choose a video", type=["mp4", "mov", "avi", "mkv"])

if uploaded:
    # --- Instant preview (stream from the in-memory upload) ---
    st.subheader("Preview")
    st.video(uploaded, width=300)# plays immediately

    # --- Save to disk ---
    save_path = UPLOADS_DIR / uploaded.name
    uploaded.seek(0)  # make sure we're at the beginning
    with open(save_path, "wb") as f:
        f.write(uploaded.read())
    st.success(f"Saved to: `{save_path}`")

    # --- Run analysis ---
    with st.spinner("Running video analysis…"):
        try:
            result = run_video_analysis(str(save_path))
            st.success("Analysis finished.")
            if isinstance(result, (dict, list)):
                st.json(result)
            elif result is not None:
                st.write(result)
        except Exception as e:
            st.error(f"Analysis failed: {e}")

    # --- Extract keyframes ---
    st.write("Extracting keyframes from the video...")
    try:
        # Prefer passing full path; fall back to fileName if your function uses that.
        try:
            run_frame_extraction(file_path=uploaded.name)
        except TypeError:
            run_frame_extraction(fileName=uploaded.name)
        st.success("Keyframe extraction finished.")
    except Exception as e:
        st.error(f"Keyframe extraction failed: {e}")

    st.info("Open **View Analysis Results** in the sidebar to view results.")
