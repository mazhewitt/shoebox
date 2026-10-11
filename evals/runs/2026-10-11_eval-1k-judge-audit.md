# Judge audit: eval-1k (Gemini vs Opus)

- Gemini: `gemini/gemini-3.8-flash` `eccbeab471df94ae`
- Opus: `claude/opus-5.5` `1e48b05a637573db`
- Sample: 11207 pairs over 186 queries graded by Opus; coverage 100.0%

## Overall

- n = 11207, exact agreement 0.823
- linear-weighted kappa 0.713 (95% CI 0.670–0.749, bootstrap over queries)
- quadratic-weighted kappa 0.794 (95% CI 0.757–0.827)

**Rule.** D10: if Gemini-Opus linear-weighted kappa < 0.6, Opus re-judges the test pool. Decision: keep the Gemini judgments

## Confusion (rows Gemini grade, columns Opus grade)

| Gemini \ Opus | 0 | 1 | 2 | n | agree |
|---|---|---|---|---|---|
| 0 | 6709 | 1367 | 7 | 8083 | 0.830 |
| 1 | 287 | 1582 | 120 | 1989 | 0.795 |
| 2 | 13 | 188 | 934 | 1135 | 0.823 |

## Per query kind

| group | n | exact | kappa lin | kappa quad |
|---|---|---|---|---|
| category | 3573 | 0.831 | 0.794 | 0.864 |
| known_item | 7634 | 0.819 | 0.622 | 0.692 |

## Per route

| group | n | exact | kappa lin | kappa quad |
|---|---|---|---|---|
| (none) | 1 | 1.000 | n/a | n/a |
| eg2-blend:e4b-bf16 | 1860 | 0.775 | 0.731 | 0.809 |
| eg2-blend:e4b-bf16+exp | 1860 | 0.771 | 0.725 | 0.804 |
| eg2-blend:e4b-bf16+exp+filt | 1860 | 0.766 | 0.724 | 0.806 |
| eg2-blend:e4b-bf16+filt | 1860 | 0.770 | 0.731 | 0.812 |
| eg2-blend:e4b-bf16-v2 | 1860 | 0.765 | 0.719 | 0.801 |
| eg2-blend:e4b-bf16-v2+exp | 1860 | 0.767 | 0.721 | 0.803 |
| eg2-blend:e4b-bf16-v2+exp+filt | 1860 | 0.768 | 0.727 | 0.809 |
| eg2-blend:e4b-bf16-v2+filt | 1860 | 0.763 | 0.723 | 0.806 |
| eg2-blend:g12b-q4 | 1860 | 0.771 | 0.726 | 0.805 |
| eg2-blend:g12b-q4+exp | 1860 | 0.774 | 0.727 | 0.805 |
| eg2-blend:g12b-q4+exp+filt | 1860 | 0.771 | 0.729 | 0.809 |
| eg2-blend:g12b-q4+filt | 1860 | 0.768 | 0.728 | 0.809 |
| eg2-blend:g12b-q4-v2 | 1860 | 0.770 | 0.726 | 0.806 |
| eg2-blend:g12b-q4-v2+exp | 1860 | 0.770 | 0.724 | 0.803 |
| eg2-blend:g12b-q4-v2+exp+filt | 1860 | 0.770 | 0.729 | 0.810 |
| eg2-blend:g12b-q4-v2+filt | 1860 | 0.766 | 0.726 | 0.808 |
| eg2-blend:gemini-medium-v2 | 1860 | 0.776 | 0.730 | 0.806 |
| eg2-blend:gemini-medium-v2+exp | 1860 | 0.775 | 0.728 | 0.805 |
| eg2-blend:gemini-medium-v2+exp+filt | 1860 | 0.769 | 0.727 | 0.807 |
| eg2-blend:gemini-medium-v2+filt | 1860 | 0.770 | 0.729 | 0.808 |
| eg2-blend:opus-v2 | 1860 | 0.776 | 0.731 | 0.808 |
| eg2-blend:opus-v2+exp | 1860 | 0.775 | 0.729 | 0.806 |
| eg2-blend:opus-v2+exp+filt | 1860 | 0.773 | 0.732 | 0.810 |
| eg2-blend:opus-v2+filt | 1860 | 0.771 | 0.730 | 0.810 |
| eg2-caption:e4b-bf16 | 1860 | 0.793 | 0.752 | 0.826 |
| eg2-caption:e4b-bf16-v2 | 1860 | 0.780 | 0.740 | 0.819 |
| eg2-caption:g12b-q4 | 1860 | 0.787 | 0.746 | 0.822 |
| eg2-caption:g12b-q4-v2 | 1860 | 0.780 | 0.737 | 0.815 |
| eg2-caption:gemini-medium-v2 | 1860 | 0.779 | 0.738 | 0.815 |
| eg2-caption:opus-v2 | 1860 | 0.770 | 0.727 | 0.807 |
| eg2-image@768 | 1860 | 0.776 | 0.732 | 0.811 |
| eg2-image@768+exp | 1860 | 0.783 | 0.738 | 0.813 |
| eg2-image@768+exp+filt | 1860 | 0.780 | 0.740 | 0.817 |
| eg2-image@768+filt | 1860 | 0.775 | 0.738 | 0.818 |
| fts:e4b-bf16 | 1860 | 0.782 | 0.731 | 0.815 |
| fts:e4b-bf16-v2 | 1860 | 0.782 | 0.728 | 0.811 |
| fts:g12b-q4 | 1860 | 0.796 | 0.749 | 0.825 |
| fts:g12b-q4-v2 | 1860 | 0.784 | 0.735 | 0.816 |
| fts:gemini-medium-v2 | 1860 | 0.794 | 0.753 | 0.827 |
| fts:opus-v2 | 1860 | 0.763 | 0.718 | 0.802 |
| hybrid-meta:e4b-bf16 | 1860 | 0.761 | 0.718 | 0.802 |
| hybrid-meta:e4b-bf16+exp | 1860 | 0.759 | 0.714 | 0.798 |
| hybrid-meta:e4b-bf16+exp+filt | 1860 | 0.762 | 0.724 | 0.809 |
| hybrid-meta:e4b-bf16+filt | 1860 | 0.769 | 0.734 | 0.817 |
| hybrid-meta:e4b-bf16-v2 | 1860 | 0.754 | 0.710 | 0.795 |
| hybrid-meta:e4b-bf16-v2+exp | 1860 | 0.759 | 0.711 | 0.796 |
| hybrid-meta:e4b-bf16-v2+exp+filt | 1860 | 0.759 | 0.718 | 0.803 |
| hybrid-meta:e4b-bf16-v2+filt | 1860 | 0.756 | 0.718 | 0.805 |
| hybrid-meta:g12b-q4 | 1860 | 0.756 | 0.709 | 0.792 |
| hybrid-meta:g12b-q4+exp | 1860 | 0.767 | 0.721 | 0.802 |
| hybrid-meta:g12b-q4+exp+filt | 1860 | 0.768 | 0.728 | 0.810 |
| hybrid-meta:g12b-q4+filt | 1860 | 0.761 | 0.721 | 0.804 |
| hybrid-meta:g12b-q4-v2 | 1860 | 0.755 | 0.709 | 0.794 |
| hybrid-meta:g12b-q4-v2+exp | 1860 | 0.756 | 0.711 | 0.797 |
| hybrid-meta:g12b-q4-v2+exp+filt | 1860 | 0.760 | 0.723 | 0.809 |
| hybrid-meta:g12b-q4-v2+filt | 1860 | 0.759 | 0.720 | 0.805 |
| hybrid-meta:gemini-medium-v2 | 1860 | 0.754 | 0.707 | 0.791 |
| hybrid-meta:gemini-medium-v2+exp | 1860 | 0.760 | 0.713 | 0.795 |
| hybrid-meta:gemini-medium-v2+exp+filt | 1860 | 0.762 | 0.722 | 0.804 |
| hybrid-meta:gemini-medium-v2+filt | 1860 | 0.756 | 0.715 | 0.800 |
| hybrid-meta:opus-v2 | 1860 | 0.758 | 0.711 | 0.795 |
| hybrid-meta:opus-v2+exp | 1860 | 0.762 | 0.716 | 0.799 |
| hybrid-meta:opus-v2+exp+filt | 1860 | 0.766 | 0.728 | 0.810 |
| hybrid-meta:opus-v2+filt | 1860 | 0.760 | 0.721 | 0.805 |
| hybrid:e4b-bf16 | 1860 | 0.767 | 0.722 | 0.805 |
| hybrid:e4b-bf16-v2 | 1860 | 0.753 | 0.704 | 0.790 |
| hybrid:g12b-q4 | 1860 | 0.763 | 0.714 | 0.796 |
| hybrid:g12b-q4-v2 | 1860 | 0.764 | 0.718 | 0.800 |
| hybrid:gemini-medium-v2 | 1860 | 0.764 | 0.715 | 0.796 |
| hybrid:opus-v2 | 1860 | 0.763 | 0.714 | 0.796 |
| image-meta | 1860 | 0.781 | 0.744 | 0.823 |
| image-meta+exp | 1860 | 0.817 | 0.778 | 0.849 |
| image-meta+exp+filt | 1860 | 0.817 | 0.785 | 0.855 |
| image-meta+filt | 1860 | 0.783 | 0.750 | 0.828 |
| meta | 1860 | 0.927 | 0.818 | 0.881 |

## Target pairs

| group | n | exact | kappa lin | kappa quad |
|---|---|---|---|---|
| targets | 134 | 0.993 | 0.855 | 0.908 |
