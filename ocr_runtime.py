"""PARSeq-Tiny inference: official PyTorch model, no Lightning/CTC/TF runtime."""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'vendor/python'), str(ROOT / 'vendor/parseq')]
import torch
from torchvision import transforms as T
from strhub.data.utils import Tokenizer
from strhub.models.parseq.model import PARSeq
from strhub.models.utils import _get_config


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize(text):
    """Plate serial only: uppercase ASCII alphanumerics, never substitute O/0 or I/1."""
    return re.sub('[^A-Z0-9]', '', text.upper())


def load_model(path, device='cpu'):
    config = _get_config('parseq-tiny')
    tokenizer = Tokenizer(config['charset_train'])
    keys = ['max_label_length', 'img_size', 'patch_size', 'embed_dim', 'enc_num_heads',
            'enc_mlp_ratio', 'enc_depth', 'dec_num_heads', 'dec_mlp_ratio', 'dec_depth',
            'decode_ar', 'refine_iters', 'dropout']
    model = PARSeq(len(tokenizer), **{k: config[k] for k in keys})
    state = torch.load(path, map_location='cpu', weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.to(device), tokenizer


transform = T.Compose([T.Resize((32, 128), T.InterpolationMode.BICUBIC),
                       T.ToTensor(), T.Normalize(0.5, 0.5)])


class Recognizer:
    def __init__(self, path, device='cpu'):
        torch.set_num_threads(4)
        self.device = device
        self.model, self.tokenizer = load_model(path, device)
        self.model.eval()

    @torch.inference_mode()
    def predict(self, images, batch_size=16):
        results = []
        for start in range(0, len(images), batch_size):
            batch = torch.stack([transform(im.convert('RGB')) for im in images[start:start + batch_size]])
            logits = self.model(self.tokenizer, batch.to(self.device))
            labels, _ = self.tokenizer.decode(logits.softmax(-1).cpu())
            results.extend({'raw': value, 'text': normalize(value)} for value in labels)
        return results
