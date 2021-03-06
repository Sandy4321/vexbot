import sys
import logging
import argparse
# import signal
import atexit
import pkg_resources
from threading import Thread

import zmq

from vexmessage import decode_vex_message

from vexbot.command_managers import AdapterCommandManager
from vexbot.adapters.messaging import ZmqMessaging # flake8: noqa

_SLEEKXMPP_INSTALLED = True

try:
    pkg_resources.get_distribution('sleekxmpp')
except pkg_resources.DistributionNotFound:
    _SLEEKXMPP_INSTALLED = False
    ClientXMPP = object

if _SLEEKXMPP_INSTALLED:
    from sleekxmpp import ClientXMPP
    from sleekxmpp.exceptions import IqError, IqTimeout


class XMPPBot(ClientXMPP):
    def __init__(self,
                 jid,
                 password,
                 room,
                 publish_address,
                 subscribe_address,
                 service_name,
                 bot_nick='EchoBot',
                 **kwargs):

        # Initialize the parent class
        if not _SLEEKXMPP_INSTALLED:
            logging.error('must install sleekxmpp')

        super().__init__(jid, password)
        self.messaging = ZmqMessaging(service_name,
                                      publish_address,
                                      subscribe_address,
                                      service_name)

        self.messaging.start_messaging()
        self.command_manager = AdapterCommandManager(self.messaging)

        self.room = room
        self.nick = bot_nick
        self.log = logging.getLogger(__file__)

        # One-shot helper method used to register all the plugins
        self._register_plugin_helper()

        self.add_event_handler("session_start", self.start)
        self.add_event_handler("groupchat_message", self.muc_message)
        self.add_event_handler('connected', self._connected)
        self.add_event_handler('disconnected', self._disconnected)
        self._thread = Thread(target=self.run)
        self._thread.daemon = True
        self._thread.start()

    def run(self):
        while True:
            frame = self.messaging.sub_socket.recv_multipart()
            message = decode_vex_message(frame)
            if message.type == 'CMD':
                self.command_manager.parse_commands(message)
            elif message.type == 'RSP':
                channel = message.contents.get('channel')
                contents = message.contents.get('response')
                self.send_message(channel, contents, mtype='groupchat')

    def _disconnected(self, *args):
        self.messaging.send_status('DISCONNECTED')

    def _connected(self, *args):
        self.messaging.send_status('CONNECTED')

    def _register_plugin_helper(self):
        """
        One-shot helper method used to register all the plugins
        """
        # Service Discovery
        self.register_plugin('xep_0030')
        # XMPP Ping
        self.register_plugin('xep_0199')
        # Multiple User Chatroom
        self.register_plugin('xep_0045')

    def start(self, event):
        self.log.info('starting xmpp')
        self.send_presence()
        self.plugin['xep_0045'].joinMUC(self.room,
                                        self.nick,
                                        wait=True)

        self.get_roster()

    def muc_message(self, msg):
        self.messaging.send_message(author=msg['mucnick'],
                                    message=msg['body'],
                                    channel=msg['from'].bare)


def _get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local', help='local arg for string parsing')
    parser.add_argument('--domain', help='domain for xmpp')
    parser.add_argument('--room', help='room!')
    parser.add_argument('--resource', help='resource')
    parser.add_argument('--password', help='password')
    parser.add_argument('--service_name')
    parser.add_argument('--publish_address')
    parser.add_argument('--subscribe_address')
    parser.add_argument('--bot_nick')

    return parser.parse_args()


def _handle_close(messaging):
    def inner(*args):
        _send_disconnect(messaging)()
        sys.exit()
    return inner


def _send_disconnect(messaging):
    def inner():
        messaging.send_status('DISCONNECTED')
    return inner


def main():
    if not _SLEEKXMPP_INSTALLED:
        logging.error('xmpp requires `sleekxmpp` installed. Please install using `pip install sleekxmpp`')

    args = _get_args()
    jid = '{}@{}/{}'.format(args.local, args.domain, args.resource)
    kwargs = vars(args)
    already_running = False

    try:
        xmpp_bot = XMPPBot(jid, **kwargs)
    except zmq.ZMQError:
        already_running = True

    if not already_running:
        messaging = xmpp_bot.messaging
        atexit.register(_send_disconnect(messaging))
        # handle_close = _handle_close(messaging)
        # signal.signal(signal.SIGINT, handle_close)
        # signal.signal(signal.SIGTERM, handle_close)

        while True:
            try:
                xmpp_bot.connect()
                xmpp_bot.process(block=True)
            except SystemExit:
                break
            except Exception as e:
                xmpp_bot.log.error(e)

if __name__ == '__main__':
    main()
