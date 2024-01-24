import os
import torch
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
from scipy.ndimage import gaussian_filter1d
from tqdm import tqdm


base_path = "/home/lidong1/yuxian/sps-toy/results/toy/linear/d128/bs512-lr0.1-tn4096-dn512-e2000/-0.5-3.0-1.0-eval_opt/10-20-7"
alpha_path = "/home/lidong1/yuxian/sps-toy/results/toy/linear/d128/bs512-lr0.1-tn4096-dn512-e2000/-0.5-3.0-1.0-opt-0.001-0/10-20-7/epoch_900/opt_alpha.pt"

IF_path = "/home/lidong1/yuxian/sps-toy/results/toy/trm/toy-trm-l2-5k-ln-ts-64/bs512-lr0.1-tn16384-dn512-e4000/-0.8_30-eval_opt/10-20-7/opt_alpha_0.4/5/all_test_IF.pt"

split = "test"

plot, ax = plt.subplots(1, 1, figsize=(5, 3))


step_min = 0
step_max = 50
all_areas, all_rate_alpha_no_zero = [], []

cm = plt.colormaps['Oranges']

alpha = torch.load(alpha_path, map_location="cpu")[step_min:step_max]
alpha = torch.clamp(alpha, min=0, max=torch.max(alpha)/30)
alpha = alpha / torch.sum(alpha, keepdim=True, dim=-1)

alpha = alpha * 1e3

alpha = alpha.numpy()

sample_num = 2048

IFs = torch.load(IF_path, map_location="cpu")
IF = torch.stack(IFs[0], dim=0)

m = torch.sum(IF[:2000] < 0, dim=0)

idxs = torch.where(m == 0)[0]

print(idxs.shape)

idx = np.random.choice(alpha.shape[1], sample_num, replace=False)
alpha = alpha[:, idx]

alpha = alpha.T
# sort alpha rows by the sum of each row
alpha = alpha[np.argsort(np.sum(alpha, axis=1))]
# alpha = alpha[np.argsort(alpha[:, 0])]


print(alpha.shape)

max_alpha = np.max(alpha)
min_alpha = np.min(alpha)

# ax.set_xscale("log")
# ax.set_yscale("log")
ax.set_xlabel(r"Training Time Steps $t$", fontsize=14)
ax.set_ylabel(r"Training Examples Index", fontsize=14)
# set the font size of x-axis and y-axis
ax.tick_params(axis='both', which='both', labelsize=14)
ax.set_yticks([0, 1000, 2000, 3000, 4000], ["0", "1k", "2k", "3k", "4k"])
# ax.set_ylim(ymin=0)
# ax.invert_xaxis()
print(alpha.shape)

ax.pcolormesh(alpha, cmap=cm, vmin=min_alpha, vmax=max_alpha)

sm = plt.cm.ScalarMappable(cmap=cm, norm=plt.Normalize(vmin=min_alpha, vmax=max_alpha))
cbar = plt.colorbar(sm, ax=ax)
cbar.ax.tick_params(labelsize=14)
cbar.set_label(r"$\gamma_{n,t}\ $" + r"$(\times 10^{-3})$", fontsize=14, labelpad=20, rotation=270)

plt.savefig(os.path.join(base_path, f"{split}_alpha.png"), bbox_inches="tight", dpi=300)
plt.close()