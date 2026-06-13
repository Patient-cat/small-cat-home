"""
Train YOLOv8-cls binary fall detector on UR Fall Detection Dataset.
Run: python train_fall_detect.py
"""
import os, shutil, random
from pathlib import Path

# ============================================================
# Config
# ============================================================
DATASET_ROOT = Path(r"D:\基于多模态AI监测的老年人跌倒风险识别及预警研究\数据集\archive\UR_fall_detection_dataset_cam0_rgb")
WORK_DIR = Path(__file__).parent / "train_output"
DATA_DIR = WORK_DIR / "data"
MODEL_NAME = "yolov8n-cls.pt"
OUTPUT_MODEL = Path(__file__).parent / "fall_detect.pt"
IMG_SIZE = 224
EPOCHS = 50
BATCH = 16
DEVICE = 0

if __name__ == '__main__':
    # ============================================================
    # Step 1: Organize data into train/val
    # ============================================================
    print("[1/4] Organizing dataset...")

    sequences = sorted([d for d in DATASET_ROOT.iterdir() if d.is_dir()])
    fall_seqs = [s for s in sequences if s.name.startswith("fall")]
    adl_seqs = [s for s in sequences if s.name.startswith("adl")]
    print(f"  Found {len(fall_seqs)} fall sequences, {len(adl_seqs)} ADL sequences")

    random.seed(42)
    random.shuffle(fall_seqs)
    random.shuffle(adl_seqs)

    fall_split = int(len(fall_seqs) * 0.8)
    adl_split = int(len(adl_seqs) * 0.8)

    splits = {
        "train": {"fall": fall_seqs[:fall_split], "normal": adl_seqs[:adl_split]},
        "val":   {"fall": fall_seqs[fall_split:], "normal": adl_seqs[adl_split:]},
    }

    for split in ["train", "val"]:
        for cls in ["fall", "normal"]:
            (DATA_DIR / split / cls).mkdir(parents=True, exist_ok=True)

    for split_name, split_data in splits.items():
        for cls_name, seqs in split_data.items():
            for seq in seqs:
                for img_file in seq.iterdir():
                    if img_file.suffix.lower() != ".png":
                        continue
                    dst = DATA_DIR / split_name / cls_name / f"{seq.name}_{img_file.name}"
                    if not dst.exists():
                        shutil.copy2(img_file, dst)

    for split in ["train", "val"]:
        n_fall = len(list((DATA_DIR / split / "fall").iterdir()))
        n_normal = len(list((DATA_DIR / split / "normal").iterdir()))
        print(f"  {split}: {n_fall} fall + {n_normal} normal = {n_fall + n_normal} images")

    # ============================================================
    # Step 2: Train
    # ============================================================
    print("\n[2/4] Training YOLOv8 classification model...")
    from ultralytics import YOLO

    model = YOLO(MODEL_NAME)
    model.train(
        data=str(DATA_DIR),
        epochs=EPOCHS,
        imgsz=IMG_SIZE,
        batch=BATCH,
        device=DEVICE,
        project=str(WORK_DIR),
        name="fall_cls",
        exist_ok=True,
        verbose=True,
        hsv_h=0.015, hsv_s=0.7, hsv_v=0.4,
        degrees=10, translate=0.1, scale=0.5,
        fliplr=0.5,
    )

    # ============================================================
    # Step 3: Validate
    # ============================================================
    print("\n[3/4] Validating...")
    metrics = model.val()
    print(f"  Top-1 Accuracy: {metrics.top1:.2%}")

    # ============================================================
    # Step 4: Export
    # ============================================================
    print(f"\n[4/4] Saving model to {OUTPUT_MODEL}...")
    model.save(str(OUTPUT_MODEL))
    print(f"  Model saved: {OUTPUT_MODEL} ({OUTPUT_MODEL.stat().st_size / 1e6:.1f} MB)")
    print("\nDone! Run app.py to use the new model.")
