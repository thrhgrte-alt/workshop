# LoRA dataset format

```
my_dataset/
  dataset.yaml
  img/001.png ...
```
```yaml
dataset: {id: coastal_ruins, trigger_word: cstruins}      # trigger_word optional: 3-31 letters/digits/underscores, starts with a letter
items:
  - {file: img/001.png, caption: "cstruins weathered stone lighthouse, golden hour, warm palette", license: own-work, owner: user, source: my sketchbook}
  - {file: img/002.png, caption: "cstruins ...", license: cc-by-4.0, owner: Jane Artist, source: "https://example.org/jane/piece (CC BY 4.0)"}
  - {file: img/003.png, caption: "cstruins ...", license: own-work, owner: user, ai_generated: true, curated: true}
```
See `examples/lora_dataset/dataset.example.yaml`. Rules (`style/lora_policy.yaml`): allowed licences own-work, licensed-for-training, cc0, cc-by-4.0 (needs `source`); every item has an `owner`; captions 8-400 characters and
containing the trigger word; short side >= 512 (warning); 15-400 items; identical files are errors, near-identical ones warnings; AI-generated items must be `curated: true` and at most 20% of the set.
The validator reads files in place and copies nothing. It cannot verify that a licence statement is true.

Validation plan: base model vs LoRA at two strengths, three held-out prompts the dataset never described, three fixed seeds (27 images). Compare palette adherence, readability, and
similarity to the training images (>= 0.95 suggests memorising), then review by eye.
