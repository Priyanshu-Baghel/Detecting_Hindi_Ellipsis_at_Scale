"""
Quick tour of the hindi_ellipsis API.
Run after training (see README):  python examples/quickstart.py models/detector models/annotator
"""
import sys

from hindi_ellipsis import Preprocessor, EllipsisDetector, TYPES, DEFINITIONS

det_dir = sys.argv[1] if len(sys.argv) > 1 else "models/detector"
ann_dir = sys.argv[2] if len(sys.argv) > 2 else None

text = ("राम ने चाय पी और सीता ने कॉफ़ी। किसी ने दरवाज़ा खटखटाया था, पता नहीं कौन। "
        "कल सुबह दिल्ली जाऊँगा। मुझे लाल वाली चाहिए। सरकार ने नई योजना शुरू की है।")

# 1. preprocessing: detector view vs. analysis view
pp = Preprocessor(annotator=ann_dir)
for s in pp(text):
    print(s.text)
    print("   words         :", s.words)
    print("   lemmas        :", s.lemmas)
    print("   content lemmas:", s.content_lemmas)

# 2. detection
det = EllipsisDetector.from_pretrained(det_dir)
for sent, dets in det.analyze_text(text):
    print("\n" + sent)
    for d in det.reconstruct(sent) if dets else []:
        print(f"  {d.type:24s} conf={d.confidence:.2f}  {d.marked}")
        if d.reconstruction:
            print(f"    reconstructed: {d.reconstruction}")
        print(f"    why: {d.reason[:160]}...")

# 3. explanation
s = "राम ने चाय पी और सीता ने कॉफ़ी।"
print("\nattributions for GAPPING_FORWARD:", det.explain(s, "GAPPING_FORWARD")[:5])
print("\nall probabilities:", det.probabilities(s))
