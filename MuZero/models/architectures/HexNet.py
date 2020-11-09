"""
In this python file we modified the class to enable generation of multiple
neural architectures by modifying the argument dictionary.

For the details of the neural architectures, we refer to our report.
:see: main_experimenter.py
"""

import sys

from keras.models import Model
from keras.layers import *
from keras.backend import constant, zeros, shape

from utils.network_utils import MinMaxScaler, Crafter

sys.path.append('../..')


class HexNNet:

    def __init__(self, game, args):
        # Network arguments
        self.board_x, self.board_y, self.planes = game.getDimensions()
        self.action_size = game.getActionSize()
        self.args = args
        self.crafter = Crafter(args)

        # s: batch_size x time x state_x x state_y
        self.observation_history = Input(shape=(self.board_x, self.board_y, self.planes * self.args.observation_length))
        # a: one hot encoded vector of shape batch_size x (state_x * state_y)
        self.action_plane = Input(shape=(self.action_size,))
        # s': batch_size  x board_x x board_y x 1
        self.encoded_state = Input(shape=(self.board_x, self.board_y, self.args.latent_depth))

        # Format action vector to plane
        omit_resign = Lambda(lambda x: x[..., :-1], output_shape=(self.board_x * self.board_y,),
                             input_shape=(self.action_size,))(self.action_plane)
        action_plane = Reshape((self.board_x, self.board_y, 1))(omit_resign)

        self.s = self.build_encoder(self.observation_history)
        self.r, self.s_next = self.build_dynamics(self.encoded_state, action_plane)
        self.pi, self.v = self.build_predictor(self.encoded_state)

        self.encoder = Model(inputs=self.observation_history, outputs=self.s, name='h')
        self.dynamics = Model(inputs=[self.encoded_state, self.action_plane], outputs=[self.r, self.s_next], name='g')
        self.predictor = Model(inputs=self.encoded_state, outputs=[self.pi, self.v], name='f')

        self.forward = Model(inputs=self.observation_history, outputs=[self.s, *self.predictor(self.s)])
        self.recurrent = Model(inputs=[self.encoded_state, self.action_plane],
                               outputs=[self.r, self.s_next, *self.predictor(self.s_next)])
        print(self.encoder.summary())
        print(self.dynamics.summary())
        print(self.predictor.summary())

    def build_encoder(self, observations):
        conv = self.crafter.conv_tower(self.args.num_convs, observations)
        res = self.crafter.conv_residual_tower(self.args.num_towers, conv,
                                               self.args.residual_left, self.args.residual_right)

        latent_state = self.crafter.activation()((
            Conv2D(self.args.latent_depth, 3, padding='same', use_bias=False)(res)))
        # latent_state = MinMaxScaler()(latent_state)

        return latent_state

    def build_dynamics(self, encoded_state, action_plane):
        stacked = Concatenate(axis=-1)([encoded_state, action_plane])
        reshaped = Reshape((self.board_x, self.board_y, 1 + self.args.latent_depth))(stacked)

        conv = self.crafter.conv_tower(self.args.num_convs, reshaped)
        res = self.crafter.conv_residual_tower(self.args.num_towers, conv,
                                               self.args.residual_left, self.args.residual_right)

        latent_state = self.crafter.activation()((
            Conv2D(self.args.latent_depth, 3, padding='same')(res)))
        latent_state = MinMaxScaler()(latent_state)

        flat = Flatten()(latent_state)

        # Cancel gradient/ predictions as r is not trained in boardgames.
        r = Dense(self.args.support_size * 2 + 1, name='r')(flat)
        r = Lambda(lambda x: x * 0)(r)

        return r, latent_state

    def build_predictor(self, latent_state):
        out_tensor = self.crafter.conv_tower(self.args.num_convs, latent_state)

        small = self.crafter.activation()((
            Conv2D(32, 3, padding='same', use_bias=False)(out_tensor)))

        flat = Flatten()(small)

        fc = self.crafter.dense_sequence(1, flat)

        pi = Dense(self.action_size, activation='softmax', name='pi')(fc)
        v = Dense(1, activation='tanh', name='v')(fc) \
            if self.args.support_size == 0 else \
            Dense(self.args.support_size * 2 + 1, activation='softmax', name='v')(fc)

        return pi, v