#!/usr/bin/python
"""
Defines a class GymProxyZmqServer that listens on a zmq port, creates an environment when connected to,
and accepts step & reset calls on that environment.

Call:
    s = GymProxyZmqServer(url, make_env)
    s.main_thr.run()

where make_env takes a string and returns an environment

"""
import math, random, time, logging, re, base64, argparse, collections, sys, os, traceback, threading
import numpy as np
import ujson
import zmq, zmq.utils.monitor
import gym
from gym.envs.proxy import zmq_serialize

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class GymProxyZmqServer(object):
    def __init__(self, url, make_env):
        self.url = url
        self.make_env = make_env
        self.env = None
        self.env_name = None

        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.REP)
        self.sock.bind(self.url)
        self.monitor_sock = self.sock.get_monitor_socket()
        self.rpc_lock = threading.Lock()
        self.rpc_rd = threading.Condition(self.rpc_lock)
        self.monitor_thr = threading.Thread(target = self.run_monitor)
        self.monitor_thr.daemon = True
        self.monitor_thr.start()
        self.main_thr = threading.Thread(target = self.run_main)

    def run_main(self):
        logger.info('zmq gym server running on %s', self.url)
        while True:
            rx = self.sock.recv_multipart(flags=0, copy=True, track=False)
            logger.debug('%s > %s', self.url, rx[0])
            self.handle_msg(rx)

    def run_monitor(self):
        logger.info('zmq gym server listening on monitoring socket')
        while True:
            ev = zmq.utils.monitor.recv_monitor_message(self.monitor_sock)
            logger.debug('Monitor Event %s', ev)
            if ev['event'] == zmq.EVENT_DISCONNECTED:
                self.closed()
            elif ev['event'] == zmq.EVENT_ACCEPTED:
                self.opened()

    def opened(self):
        self.env_name = None
        self.op_count = 0
        logger.info('GymProxyZmqServer opened')

    def closed(self):
        logger.info('GymProxyZmqServer closed')
        self.env_name = None
        if self.env is not None:
            self.env.close()
        self.env = None

    def handle_msg(self, rx):
        rpc = zmq_serialize.load_msg(rx)
        rpc_method = rpc.get('method', None)
        rpc_params = rpc.get('params', None)

        def reply(rpc_result, rpc_error=None):
            tx = zmq_serialize.dump_msg({
                'result': rpc_result,
                'error': rpc_error,
            })
            logger.debug('%s < %s', self.url, tx[0])
            self.sock.send_multipart(tx, flags=0, copy=False, track=False)

        self.op_count += 1
        if self.op_count % 1000 == 0:
            logger.info('%s: %d ops', self.env_name, self.op_count)
        try:
            if rpc_method == 'step':
                reply(self.handle_step(rpc_params))
            elif rpc_method == 'reset':
                reply(self.handle_reset(rpc_params))
            elif rpc_method == 'setup':
                reply(self.handle_setup(rpc_params))
            elif rpc_method == 'close':
                reply(self.handle_close(rpc_params))
            elif rpc_method == 'render':
                reply(self.handle_render(rpc_params))
            else:
                raise Exception('unknown method %s' % rpc_method)
        except:
            ex_type, ex_value, ex_tb = sys.exc_info()
            traceback.print_exception(ex_type, ex_value, ex_tb)
            reply(None, str(ex_type) + ': ' + str(ex_value))

    def handle_reset(self, params):
        if params['session_id'] != self.session_id:
            raise Exception('Wrong session id')
        obs = self.env.reset()
        return {
            'obs': obs,
            'session_id': self.session_id,
        }

    def handle_step(self, params):
        if params['session_id'] != self.session_id:
            raise Exception('Wrong session id')
        action = params['action']
        obs, reward, done, info = self.env.step(action)
        return {
            'obs': obs,
            'reward': reward,
            'done': done,
            'info': info,
            'session_id': self.session_id,
        }

    def handle_setup(self, params):
        if self.env_name is not None:
            raise Exception('Already set up')
        self.env = self.make_env(params['env_name'])
        self.env_name = params['env_name']
        self.session_id = zmq_serialize.mk_random_cookie()
        logger.info('Creating env %s. session_id=%s', self.env_name, self.session_id)

        return {
            'observation_space': self.env.observation_space,
            'action_space' : self.env.action_space,
            'reward_range': self.env.reward_range,
            'session_id': self.session_id,
        }

    def handle_close(self, params):
        if params['session_id'] != self.session_id:
            raise Exception('Wrong session id')
        self.env_name = None
        if self.env:
            self.env.close()
        self.env = None
        return {
            'session_id': self.session_id,
        }

    def handle_render(self, params):
        if params['session_id'] != self.session_id:
            raise Exception('Wrong session id')
        mode = params['mode']
        close = params['close']
        img = self.env.render(mode, close)
        return {
            'img': img,
            'session_id': self.session_id,
        }

# If invoked directory, serve any environment
if __name__ == '__main__':
    def make_env(name):
        return gym.make(name)
    zmqs = GymProxyZmqServer('tcp://127.0.0.1:6911', make_env)
    zmqs.main_thr.run()
