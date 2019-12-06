from sklearn import model_selection
import pandas
import pickle
import numpy as np
import time
import random
from operator import itemgetter
from tqdm import tqdm
from tqdm import trange
import yaml

# import spacy
# spacy_en = spacy.load('en')

import torch
import torch.nn as nn
import torch.utils.data
from models import CATModel

train_dataset = pickle.load(open('../data/labelled_dataset_train.p', 'rb'))
valid_dataset = pickle.load(open('../data/labelled_dataset_valid.p', 'rb'))
test_dataset = pickle.load(open('../data/labelled_dataset_test.p', 'rb'))

codes = pickle.load(open('../data/codes','rb'))
annos = pickle.load(open('../data/annos','rb'))
asts = pickle.load(open('../data/asts','rb'))

trainDF = {}
trainDF['code'] = codes
trainDF['anno'] = annos
trainDF['ast'] = asts


with open("../config.yml", 'r') as config_file:
    cfg = yaml.load(config_file, Loader=yaml.FullLoader)

random_seed = cfg["random_seed"]
np.random.seed(random_seed)
embedding_dim = cfg["embedding_dim"]
learning_rate = cfg["learning_rate"]
seq_len_anno = 0
seq_len_code = 0
hidden_size = cfg["hidden_size"]
dense_dim = cfg["dense_dim"]
output_dim = cfg["output_dim"]
num_layers_lstm = cfg["num_layers_lstm"]
use_cuda = cfg["use_cuda"]
batch_size = cfg["batch_size"]
# n_iters = 4000
# num_epochs = n_iters / (len(train_dataset) / batch_size)
# num_epochs = int(num_epochs)
num_epochs = cfg["epochs"]
use_softmax_classifier = cfg["use_softmax_classifier"]
use_bin = cfg["use_bin"]
use_bidirectional = cfg["use_bidirectional"]
use_adam = cfg["use_adam"]
use_parallel = cfg["use_parallel"]
save_path = cfg["save_path"]
if use_cuda:
    device_id = 0
    torch.cuda.set_device(device_id)


# Loading word embeddings
if use_bin:
    import fastText.FastText as ft
    ft_anno_vec = ft.load_model('conala/ft_models/anno_model.bin')
    ft_code_vec = ft.load_model('conala/ft_models/code_model.bin')
else:
    from keras.preprocessing import text, sequence

# def tokenizer(text): # create a tokenizer function
#     return [tok.text for tok in spacy_en.tokenizer(text)]


def prepare_sequence(seq, seq_len, to_ix):
    idxs_list = []
    
    for seq_elem in seq:
        idxs = []
        for w in seq_elem.split():
            try:
                idxs.append(to_ix[w])
            except KeyError:
                continue
        # idxs = [to_ix[w] for w in seq_elem.split()]
        # idxs = [to_ix[w] for w in tokenizer(seq_elem)]
        idxs.reverse()
        if len(idxs) > seq_len:
            idxs = idxs[:seq_len]
        while len(idxs) < seq_len:
            idxs.append(0)
        idxs.reverse()
        idxs_list.append(idxs)
    return torch.tensor(idxs_list, dtype=torch.long)


def create_embeddings(fname, embed_type):
    embeddings_index = {}
    for i, line in enumerate(open(fname)):
        values = line.split()
        embeddings_index[values[0]] = np.asarray(values[1:], dtype='float32')

    # create a tokenizer
    token = text.Tokenizer(char_level=False)
    token.fit_on_texts(trainDF[embed_type])
    word_index = token.word_index

    # convert text to sequence of tokens and pad them to ensure equal length vectors
    # train_seq_x = sequence.pad_sequences(token.texts_to_sequences(train_x), maxlen=seq_len)
    # valid_seq_x = sequence.pad_sequences(token.texts_to_sequences(valid_x), maxlen=seq_len)

    # create token-embedding mapping
    embedding_matrix = np.zeros((len(word_index) + 1, 300))
    for word, i in word_index.items():
        embedding_vector = embeddings_index.get(word)
        if embedding_vector is not None:
            embedding_matrix[i] = embedding_vector

    return word_index, embedding_matrix


# Create word-index mapping

seq_len_code = seq_len_anno = seq_len_ast = 300

load_var = True

if not load_var:
    word_to_ix_anno, weights_matrix_anno = create_embeddings('../saved_models/anno_model.vec', 'anno')
    word_to_ix_code, weights_matrix_code = create_embeddings('../saved_models/code_model.vec', 'code')
    word_to_ix_ast, weights_matrix_ast = create_embeddings('../saved_models/ast_model.vec', 'ast')
    weights_matrix_anno = torch.from_numpy(weights_matrix_anno)
    weights_matrix_code = torch.from_numpy(weights_matrix_code)
    weights_matrix_ast = torch.from_numpy(weights_matrix_ast)
else:
    word_to_ix_anno, weights_matrix_anno = pickle.load(open("../variables/anno_var",'rb'))
    word_to_ix_code, weights_matrix_code = pickle.load(open("../variables/code_var",'rb'))
    word_to_ix_ast, weights_matrix_ast = pickle.load(open("../variables/ast_var",'rb'))

def create_emb_layer(weights_matrix, non_trainable=False):
    num_embeddings, embedding_dim = weights_matrix.size()
    emb_layer = nn.Embedding(num_embeddings, embedding_dim)
    emb_layer.load_state_dict({'weight': weights_matrix})
    if non_trainable:
        emb_layer.weight.requires_grad = False

    return emb_layer, num_embeddings, embedding_dim



sim_model = CATModel(weights_matrix_anno, hidden_size, num_layers_lstm, dense_dim, output_dim, weights_matrix_code, 
    weights_matrix_ast)

if torch.cuda.is_available() and use_cuda:
    sim_model.cuda()

if cfg["encoder"] == 'LSTM':
    print("Encoder Type: LSTM")
    sim_model.load_state_dict(torch.load(f"../{save_path}/sim_model_cat"))
elif cfg["encoder"] == 'Transformer':
    print("Encoder Type: Transformer")
    sim_model.load_state_dict(torch.load(f"../{save_path}/sim_model_ast_transformer"))

test_dataset = test_dataset[:1000]

train_loader = torch.utils.data.DataLoader(dataset=train_dataset,
                                           batch_size=batch_size,
                                           shuffle=True)

test_loader = torch.utils.data.DataLoader(dataset=test_dataset,
                                          batch_size=batch_size,
                                          shuffle=False)

ret_loader = torch.utils.data.DataLoader(dataset=test_dataset,
                                          batch_size=1,
                                          shuffle=False)

# test_loader = torch.utils.data.DataLoader(dataset=test_dataset,
#                                           batch_size=1,
#                                           shuffle=False)





# Testing

def eval_matching():
    sim_model.eval()
    if use_softmax_classifier:
        dense_model.eval()

    con_mat = np.zeros((2, 2), dtype=int)

    csv_out = pandas.DataFrame(columns=['Code', 'Anno', 'Score', 'Label'])
    for i, (code_sequence, ast_sequence, anno_sequence, labels) in enumerate(tqdm(test_loader)):
        anno_in = prepare_sequence(anno_sequence, seq_len_anno, word_to_ix_anno)
        code_in = prepare_sequence(code_sequence, seq_len_code, word_to_ix_code)
        if torch.cuda.is_available() and use_cuda:
            sim_score, _, _ = sim_model(anno_in.cuda(), code_in.cuda())
        else:
            sim_score, _, _ = sim_model(anno_in, code_in)
        
        if torch.cuda.is_available() and use_cuda:
            labels = labels.cuda()
        cos = nn.CosineSimilarity(dim=1, eps=1e-10)
        dist = nn.modules.distance.PairwiseDistance(p=1, eps=1e-10)
        sim_score = 1.0-dist(anno_vector, code_vector)
        
        # csv_out = csv_out.append({'Code': ' '.join(code_sequence[0]), 'Anno': ' '.join(anno_sequence[0]), 'Score': sim_score.detach().cpu().numpy()[0], 'Label': labels.detach().cpu().numpy()[0]}, ignore_index = True)
        

        if i == 0:
            total_score = sim_score
            total_labels = labels
        else:
            total_score = torch.cat((total_score, sim_score))
            total_labels = torch.cat((total_labels, labels))

    sim_score = total_score
    labels = total_labels
    maxacc = 0.0
    thres = 0
    pred_max = []
    for t in trange(1, 100):
        pred_temp = []
        curr_thres = t/100.0
        pred_tensor = torch.ge(sim_score, curr_thres).long()
        # curr_acc = (torch.sum(pred_tensor == labels).item())/100.0
        curr_acc = torch.mean((pred_tensor == labels).float()).item()

        if curr_acc > maxacc:
            maxacc = curr_acc
            best_tensor = pred_tensor
            best_thres = curr_thres
    predicted = best_tensor
    for p, t in zip(predicted, labels):
        con_mat[t][p] += 1

    # csv_out.to_csv(r'results_torch_classifier.csv', index = None, header=True))
    con_matdf = pandas.DataFrame(con_mat)
    print('Confusion Matrix:')
    print(con_matdf)
    acc = (con_mat[0][0]+con_mat[1][1])/(con_mat[0][0]+con_mat[1][1]+con_mat[0][1]+con_mat[1][0])
    prec = con_mat[1][1]/(con_mat[0][1]+con_mat[1][1])
    rec = con_mat[1][1]/(con_mat[1][0]+con_mat[1][1])
    if not use_softmax_classifier:
        print("Threshold = ", best_thres)
    print("Accuracy = ", acc)
    print('Precision = ', prec)
    print('Recall = ', rec)


def eval_retrieval():
    mrr = 0
    count = 0
    r1 = 0
    r5 = 0
    r10 = 0

    # outp = open("results_rank_torch.txt","w")
    sim_model.eval()
    with torch.no_grad():
        for i, (code_sequence, ast_sequence, anno_sequence, distractor_list) in enumerate(tqdm(ret_loader)):
            # print("Idx = ", i)
            anno_in = prepare_sequence(anno_sequence, seq_len_anno, word_to_ix_anno)
            ranked_list = []
            codebase = []
            codebase.append((code_sequence[0], ast_sequence[0]))
            count_dist = 0
            for code_dist, ast_dist in distractor_list:
                count_dist += 1
                if count_dist >= 99:
                    break
                codebase.append((code_dist[0], ast_dist[0]))
            if torch.cuda.is_available() and use_cuda:
                anno_in = anno_in.cuda()
            # anno_vector = anno_model(anno_in)
            for cand_code, cand_ast in codebase:
                cand_code = (cand_code,)
                cand_ast = (cand_ast,)
                code_in = prepare_sequence(cand_code, seq_len_code, word_to_ix_code)
                ast_in = prepare_sequence(cand_ast, seq_len_ast, word_to_ix_ast)
                
                if torch.cuda.is_available() and use_cuda:
                    code_in = code_in.cuda()
                    ast_in = ast_in.cuda()
                
                sim_score, _, _ = sim_model(anno_in, code_in, ast_in)

                # code_vector = code_model(code_in)
                # if torch.cuda.is_available() and use_cuda:
                #     labels = labels.cuda()
                # cos = nn.CosineSimilarity(dim=1, eps=1e-10)
                # dist = nn.modules.distance.PairwiseDistance(p=1, eps=1e-10)
                # sim_score = 1.0-dist(anno_vector, code_vector)
                
                sim_score = sim_score.item()
                ranked_list.append((cand_code, sim_score))
            ranked_list = sorted(ranked_list, key=itemgetter(1),reverse=True)
            # ranked_list = ranked_list[:11]
            code_sequence = code_sequence[0]
            rank = 0
            for i, item in enumerate(ranked_list):
                if item[0][0]==code_sequence and rank==0:
                    rank = i+1
            if not rank:
                count += 1
                continue
                # print("No Rank")
                # exit()
            mrr += 1.0/(rank)
            if rank == 1:
                r1 += 1
            if rank <= 5:
                r5 += 1
                # outp.write("Rank = {}\n".format(rank))
                # outp.write(' '.join(anno_sequence[0]))
                # outp.write("\n")
                for i in range(5):
                    code = ranked_list[i][0]
                    # outp.write(' '.join(code[0]))
                    # outp.write("\n")
                # outp.write("\n")
            if rank <= 10:
                r10 += 1
            count += 1

    # outp.close()
    mrr /= count
    r1 /= count
    r5 /= count
    r10 /= count
    with open("../results/results_CAT_1layer_lstm.txt","w") as f:
        f.write(f"MRR = {mrr}\n")
        f.write(f"Recall@1 = {r1}\n")
        f.write(f"Recall@5 = {r5}\n")
        f.write(f"Recall@10 = {r10}\n")
    print("MRR = ", mrr)
    print("Recall@1 = ",r1)
    print("Recall@5 = ",r5)
    print("Recall@10 = ",r10)


eval_retrieval()
