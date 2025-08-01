import torch
import torch.nn as nn
import math

class InputEmbedding(nn.Module):
    def __init__(self , d_model:int , vocab_size:int):
        super().__init__()
        self.d_model = d_model
        self.vocab_size = vocab_size
        self.embedding = nn.Embedding(vocab_size , d_model)

    def forward(self ,x):
        return self.embedding(x)*math.sqrt(self.d_model)

class PositionalEncoding(nn.Module):
    def __init__(self , d_model:int , seq_length:int , dropout:float):
        super().__init__()
        self.d_model = d_model
        self.seq_length = seq_length
        self.dropout = nn.Dropout(dropout)

        #creating a matrix of size (d_model , seq_length)
        self.pe = torch.zeros(seq_length , d_model)

        #Creating a vector of shape (Seq_len , 1)
        position = torch.arange(0 , seq_length , dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0 , d_model , 2).float()*(-math.log(10000.0)/d_model))

        #Apply the sin and cos to even and odd positions respectively
        self.pe[: , ::2] = torch.sin(position*div_term)
        self.pe[: , 1::2] = torch.cos(position*div_term)

        self.pe = self.pe.unsqueeze(0) 
        
        self.register_buffer("pe" , self.pe)

    def forward(self , x):
        x = x + (self.pe[: , :x.shape[1] , :]).requires_grad_(False)
        return self.dropout(x)

class LayerNorm(nn.Module):
    def __init__(self, eps:float = 10**-6):
        super().__init__()
        self.eps = eps
        self.alpha = nn.Parameter(torch.ones(1))
        self.gamma = nn.Parameter(torch.zeros(1))

    def forward(self , x):
        mean = x.mean(dim=-1 , keepdim=True)
        std = x.std(dim=-1 , keepdim=True)
        return self.alpha*(x - mean) / (std + self.eps) + self.gamma
    
class FeedForwardBlock(nn.Module):
    def __init__(self , d_model:int , d_ff:int , dropout):
        super().__init__()
        self.Linear_1 = nn.Linear(d_model , d_ff)
        self.dropout = nn.Dropout(dropout)
        self.Linear_2 = nn.Linear(d_ff , d_model)

    def forward(self , x):
        return self.Linear_2(self.dropout(torch.relu(self.Linear_1(x))))
    
class MultiAttentionBlock(nn.Module):
    def __init__(self , d_model:int , h:int , dropout:float):
        super().__init__()
        self.d_model = d_model
        self.h = h
        assert d_model % h == 0 , "d_model is not divisible by h"
        self.d_k = d_model // h
        self.w_q = nn.Linear(d_model , d_model) 
        self.w_k = nn.Linear(d_model , d_model)
        self.w_v = nn.Linear(d_model , d_model)
        self.w_o = nn.Linear(d_model , d_model)
        self.dropout = nn.Dropout(dropout)

    @staticmethod
    def attention( query , key , value , mask , dropout:nn.Dropout):
        d_k = query.shape[-1]
        attention_score = (query@key.transpose(-2 , -1))/math.sqrt(d_k)
        if mask is not None:
            attention_score = attention_score.masked_fill_(mask == 0 , -1e9)
        attention_score = attention_score.softmax(-1)
        if dropout is not None:
            attention_score = dropout(attention_score)
        return (attention_score@value) , attention_score
        
    def forward(self ,q , k , v,  mask):
        #(batch_size , seq_lentgh , d_model) --> (batch_size , seq_length , d_model)
        query = self.w_q(q)
        key = self.w_k(k)
        value = self.w_v(v)

        #(batch_size , seq_lentgh , d_model) --> (batch_size , seq_length , self.h , self.d_k) --> (batch_size , self.h,  seq_length  , self.d_k)
        query = query.view(query.shape[0] , query.shape[1] , self.h , self.d_k ).transpose(1 , 2)
        key = key.view(key.shape[0] , key.shape[1] , self.h , self.d_k).transpose(1,2)
        value = value.view(value.shape[0] , value.shape[1] , self.h , self.d_k).transpose(1,2)

        x , attention_score = MultiAttentionBlock.attention(query , key , value , mask , self.dropout)

        return self.w_o(x)

class ResidualConnection(nn.Module):
    def __init__(self  , dropout:float):
        super().__init__()
        self.norm = LayerNorm()
        self.dropout = nn.Dropout(dropout)

    def forward(self , x , sublayer):
        return x + self.dropout(sublayer(self.norm(x)))
    
class EncoderBlock(nn.Module):
    def __init__(self , multi_head_attention:MultiAttentionBlock , feed_forward_block :FeedForwardBlock , dropout: float ):
        super().__init__()
        self.multi_head_attention = multi_head_attention
        self.feedforward = feed_forward_block
        self.residual = nn.ModuleList([ResidualConnection(dropout) for _ in range(2)])

    def forward(self , x , src_mask):
        x = self.residual[0](x , lambda x:self.multi_head_attention(x,x,x,src_mask))
        x = self.residual[1](x , self.feedforward)
        return x
class Encoder(nn.Module):
    def __init__(self , layers: nn.ModuleList):
        super().__init__()
        self.layers = layers
        self.norm = LayerNorm()
    
    def forward(self,x,mask):
        for layer in self.layers:
            x = layer(x , mask)
        
        return self.norm(x)
    
class DecoderBlock(nn.Module):
    def __init__(self , self_attention:MultiAttentionBlock , cross_attention : MultiAttentionBlock , feed_forward: FeedForwardBlock , dropout:float):
        super().__init__()
        self.feed_forward = feed_forward
        self.self_attention = self_attention
        self.cross_attention = cross_attention
        self.residual = nn.ModuleList([ResidualConnection(dropout) for _ in range(3)])

    def forward(self , encoder_output , x , tgt_mask , src_mask):
        x = self.residual[0](x , lambda x:self.self_attention(x,x,x,tgt_mask))
        x = self.residual[1](x , lambda x:self.cross_attention(encoder_output , encoder_output , x , src_mask))
        x = self.residual[2](x , self.feed_forward)
        return x

class Decoder(nn.Module):
    def __init__(self , layers:nn.ModuleList):
        super().__init__()
        self.layers = layers
        self.norm = LayerNorm()

    def forward(self , x ,encoder_output, src_mask , tgt_mask):
        for layer in self.layers:
            x = layer(x ,encoder_output ,src_mask , tgt_mask)
        return self.norm(x)

class ProjectionLayer(nn.Module):
    def __init__(self , d_model:int , vocab_size:int):
        super().__init__()
        self.proj = nn.Linear(d_model , vocab_size)

    def forward(self , x):
        #(Batch_size , seq_length , d_model) -->  #(Batch_size , seq_length , vocab_size) 
        return torch.log_softmax(self.proj(x) , dim=-1)


class Transformer(nn.Module):
    def __init__(self , encoder:Encoder , decoder:Decoder , src_emb:InputEmbedding , tgt_emb:InputEmbedding , src_pos:PositionalEncoding , tgt_pos:PositionalEncoding , projection:ProjectionLayer):
        super().__init__()
        self.encoder = encoder
        self.decoder = decoder
        self.src_emb = src_emb
        self.tgt_emb = tgt_emb
        self.src_pos = src_pos
        self.tgt_pos = tgt_pos
        self.projection = projection

    def encode(self , src , src_mask):
        src = self.src_emb(src)
        src = self.src_pos(src)
        return self.encoder(src , src_mask)
    
    def decode(self, encoder_output, src_mask, tgt, tgt_mask):
        # (batch, seq_len, d_model)
        tgt = self.tgt_emb(tgt)
        tgt = self.tgt_pos(tgt)
        return self.decoder(tgt, encoder_output, src_mask, tgt_mask)
    
    def project(self, x):
        # (batch, seq_len, vocab_size)
        return self.projection(x)

def build_transformer(src_vocab_size: int, tgt_vocab_size: int, src_seq_len: int, tgt_seq_len: int, d_model: int=512, N: int=6, h: int=8, dropout: float=0.1, d_ff: int=2048) -> Transformer:
    # Create the embedding layers
    src_embed = InputEmbedding(d_model, src_vocab_size)
    tgt_embed = InputEmbedding(d_model, tgt_vocab_size)

    # Create the positional encoding layers
    src_pos = PositionalEncoding(d_model, src_seq_len, dropout)
    tgt_pos = PositionalEncoding(d_model, tgt_seq_len, dropout)
    
    # Create the encoder blocks
    encoder_blocks = []
    for _ in range(N):
        encoder_self_attention_block = MultiAttentionBlock(d_model, h, dropout)
        feed_forward_block = FeedForwardBlock(d_model, d_ff, dropout)
        encoder_block = EncoderBlock(encoder_self_attention_block, feed_forward_block, dropout)
        encoder_blocks.append(encoder_block)

    # Create the decoder blocks
    decoder_blocks = []
    for _ in range(N):
        decoder_self_attention_block = MultiAttentionBlock(d_model, h, dropout)
        decoder_cross_attention_block = MultiAttentionBlock(d_model, h, dropout)
        feed_forward_block = FeedForwardBlock(d_model, d_ff, dropout)
        decoder_block = DecoderBlock(decoder_self_attention_block, decoder_cross_attention_block, feed_forward_block, dropout)
        decoder_blocks.append(decoder_block)
    
    # Create the encoder and decoder
    encoder = Encoder(nn.ModuleList(encoder_blocks))
    decoder = Decoder(nn.ModuleList(decoder_blocks))
    
    # Create the projection layer
    projection_layer = ProjectionLayer(d_model, tgt_vocab_size)
    
    # Create the transformer
    transformer = Transformer(encoder, decoder, src_embed, tgt_embed, src_pos, tgt_pos, projection_layer)
    
    # Initialize the parameters
    for p in transformer.parameters():
        if p.dim() > 1:
            nn.init.xavier_uniform_(p)
    
    return transformer