import os
import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style="white", context="notebook")
plt.rcParams['font.weight'] = '500'
plt.rcParams['axes.labelweight'] = 'bold'
plt.rcParams['axes.titleweight'] = 'bold'

results_dir = '../test_results/'
categories = ['Phishing', 'Legitimate'] 
master_data = []

model_names = {
    'cnn_bilstm': 'CNN + BiLSTM',
    'rnn_base': 'RNN Baseline',
    'ann_base': 'ANN Baseline',
    'nb': 'Naive Bayes',
    'rf': 'Random Forest',
    'svm': 'Support Vector Machine'
}

print("🚀 Starting Comprehensive Graph Generation...\n")

for folder in os.listdir(results_dir):
    folder_path = os.path.join(results_dir, folder)
    if not os.path.isdir(folder_path): continue
    
    # Matches your folder name safely
    folder_lower = folder.lower().strip()
    pretty_name = model_names.get(folder_lower, folder.upper())
    
    json_path = os.path.join(folder_path, 'raw_test_results.json')
    report_path = os.path.join(folder_path, 'classification_report.txt')
    
    if os.path.exists(json_path):
        print(f"Extracting data and plotting for: {pretty_name}...")
        with open(json_path, 'r', encoding='utf-8') as f:
            res = json.load(f)
            
        summary_text = res.get('hiperparameter', {}).get('model_summary', '')
        if summary_text:
            with open(os.path.join(folder_path, 'model_summary.txt'), 'w', encoding='utf-8') as f:
                f.write(summary_text)
                
        history = res.get('epoch_history', {})
        
        if 'accuracy' in history and 'val_accuracy' in history:
            epochs = range(1, len(history['accuracy']) + 1)
            
            fig, ax = plt.subplots(figsize=(10, 6), dpi=300)
            ax.plot(epochs, history['accuracy'], color='#3b82f6', linewidth=3, marker='o', markersize=6, label='Training Accuracy')
            ax.plot(epochs, history['val_accuracy'], color='#f59e0b', linewidth=3, marker='s', markersize=6, label='Validation Accuracy')
            ax.fill_between(epochs, history['accuracy'], alpha=0.1, color='#3b82f6')
            ax.fill_between(epochs, history['val_accuracy'], alpha=0.1, color='#f59e0b')
            ax.set_title(f'{pretty_name}\nAccuracy Progression', fontsize=18, pad=20)
            ax.set_xlabel('Epochs', fontsize=14)
            ax.set_ylabel('Accuracy', fontsize=14)
            ax.grid(axis='y', linestyle='--', alpha=0.5)
            sns.despine(left=True, bottom=True)
            ax.legend(frameon=False, loc='lower right')
            plt.tight_layout()
            plt.savefig(os.path.join(folder_path, 'accuracy.png'))
            plt.close()
            
            fig, ax = plt.subplots(figsize=(10, 6), dpi=300)
            ax.plot(epochs, history['loss'], color='#ef4444', linewidth=3, marker='o', markersize=6, label='Training Loss')
            ax.plot(epochs, history['val_loss'], color='#10b981', linewidth=3, marker='s', markersize=6, label='Validation Loss')
            ax.fill_between(epochs, history['loss'], alpha=0.1, color='#ef4444')
            ax.fill_between(epochs, history['val_loss'], alpha=0.1, color='#10b981')
            ax.set_title(f'{pretty_name}\nLoss Reduction', fontsize=18, pad=20)
            ax.set_xlabel('Epochs', fontsize=14)
            ax.set_ylabel('Loss', fontsize=14)
            ax.grid(axis='y', linestyle='--', alpha=0.5)
            sns.despine(left=True, bottom=True)
            ax.legend(frameon=False, loc='upper right')
            plt.tight_layout()
            plt.savefig(os.path.join(folder_path, 'loss.png'))
            plt.close()
            
        cm = res.get('test_result', {}).get('test_confusion_matrix')
        if cm:
            cm_array = np.array(cm)
            
            fig, ax = plt.subplots(figsize=(8, 6), dpi=300)
            sns.heatmap(cm_array, annot=True, fmt='d', cmap='mako_r', cbar=False, 
                        annot_kws={"size": 18, "weight": "bold"}, 
                        linewidths=3, linecolor='white', xticklabels=categories, yticklabels=categories, ax=ax)
            ax.set_title(f'{pretty_name}\nConfusion Matrix', fontsize=18, pad=20)
            ax.set_xlabel('Predicted Label', fontsize=14, labelpad=15)
            ax.set_ylabel('True Label', fontsize=14, labelpad=15)
            plt.tight_layout()
            plt.savefig(os.path.join(folder_path, 'confusion_matrix.png'))
            plt.close()
            
            cm_normalized = cm_array.astype('float') / cm_array.sum(axis=1)[:, np.newaxis]
            fig, ax = plt.subplots(figsize=(8, 6), dpi=300)
            sns.heatmap(cm_normalized, annot=True, fmt='.2%', cmap='mako_r', cbar=False, 
                        annot_kws={"size": 16, "weight": "bold"}, 
                        linewidths=3, linecolor='white', xticklabels=categories, yticklabels=categories, ax=ax)
            ax.set_title(f'{pretty_name}\nNormalized Confusion Matrix', fontsize=18, pad=20)
            ax.set_xlabel('Predicted Label', fontsize=14, labelpad=15)
            ax.set_ylabel('True Label', fontsize=14, labelpad=15)
            plt.tight_layout()
            plt.savefig(os.path.join(folder_path, 'normalized_confusion_matrix.png'))
            plt.close()
            
        acc = res.get('test_result', {}).get('test_acc', 0) * 100
        master_data.append({'Model': pretty_name, 'Type': 'Deep Learning', 'Accuracy': acc})

    elif os.path.exists(report_path):
        print(f"Extracting data for: {pretty_name}...")
        with open(report_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('Accuracy:'):
                    acc = float(line.split(':')[1].strip()) * 100
                    master_data.append({'Model': pretty_name, 'Type': 'Traditional ML', 'Accuracy': acc})
                    break

print("\n📊 Generating Master Comparison Graph...")
df = pd.DataFrame(master_data).sort_values(by='Accuracy', ascending=False)
if not df.empty:
    fig, ax = plt.subplots(figsize=(12, 7), dpi=300)
    
    colors = ['#0ea5e9' if 'Proposed' in x else ('#6366f1' if y == 'Deep Learning' else '#94a3b8') for x, y in zip(df['Model'], df['Type'])]
    
    sns.barplot(x='Model', y='Accuracy', data=df, palette=colors, ax=ax, edgecolor="none", zorder=3)
    ax.set_title('Final Model Accuracy Comparison', fontsize=22, pad=25)
    ax.set_xlabel('Architecture', fontsize=16)
    ax.set_ylabel('Test Set Accuracy (%)', fontsize=16, labelpad=15)
    ax.set_ylim(40, 105) 
    ax.grid(axis='y', linestyle='--', alpha=0.6, zorder=0)
    sns.despine(left=True, bottom=False)
    
    for p in ax.patches:
        height = p.get_height()
        if not np.isnan(height):
            ax.annotate(f"{height:.2f}%", (p.get_x() + p.get_width() / 2., height), 
                        ha='center', va='bottom', fontsize=14, fontweight='bold', color='white', 
                        xytext=(0, 8), textcoords='offset points',
                        bbox=dict(facecolor='#1e293b', edgecolor='none', boxstyle='round,pad=0.3', alpha=0.8))
            
    master_graph_path = os.path.join(results_dir, 'DL vs ML Models Comparison.png')
    plt.tight_layout()
    plt.savefig(master_graph_path, bbox_inches='tight')
    plt.close()

print("\n Complete!")