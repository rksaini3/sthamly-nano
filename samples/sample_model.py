import torch
import torch.nn as nn
from torchvision import datasets, transforms
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CKPT = ROOT / "samples" / "mnist_mlp.pt"


class MLP(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Flatten(),
            nn.Linear(784, 128),
            nn.ReLU(),
            nn.Linear(128, 10),
        )

    def forward(self, x):
        return self.net(x)


def train(epochs: int = 3):
    ds = datasets.MNIST(str(ROOT / "data" / "mnist"), train=True,
                        download=True, transform=transforms.ToTensor())
    dl = torch.utils.data.DataLoader(ds, batch_size=128, shuffle=True)
    model = MLP()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    for ep in range(epochs):
        for x, y in dl:
            opt.zero_grad()
            loss_fn(model(x), y).backward()
            opt.step()
        print(f"epoch {ep + 1}/{epochs} done")
    torch.save(model.state_dict(), CKPT)
    print("saved", CKPT)


if __name__ == "__main__":
    train()