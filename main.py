import datetime
import io
import json
import logging
import time

import PIL.ImageOps
import matplotlib as mpl

mpl.use('Agg')
import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np
import pymysql

pymysql.install_as_MySQLdb()
import pytz
import requests
from PIL import Image
from scipy.interpolate import interp1d
from scipy.ndimage.filters import gaussian_filter

import config
import query

logging.basicConfig(format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s', level=logging.INFO)

tz = pytz.timezone(config.TIMEZONE)


class Database():
    def __enter__(self):
        self.conn = pymysql.connect(host=config.MYSQL_HOST, user=config.MYSQL_USER, passwd=config.MYSQL_PASS,
                                    db=config.MYSQL_DB)
        self.cursor = self.conn.cursor()
        return self

    def execute(self, query):
        class Wrapper():
            def __init__(self, cursor, query):
                self.cursor = cursor
                self.cursor.execute(query)

            def fetchall(self):
                columns = [col[0] for col in self.cursor.description]
                return [dict(zip(columns, row)) for row in self.cursor.fetchall()]

        return Wrapper(self.cursor, query)

    def __exit__(self, type, value, traceback):
        self.conn.close()


class Heatmap():
    def __init__(self, map, mode, id=None):
        self.map = map
        self.mode = mode
        self.id = id if id else None
        self.map_width = 1024
        self.map_height = 1024

        with Database() as db:
            extents = json.loads(db.execute(query.MAP_EXTENT.format(self.map)).fetchall()[0]['minimapExtents'])

        self.o_x, self.o_y, self.o_z = [float(i) for i in extents['origin'].split(' ')]
        self.s_x, self.s_y, self.s_z = [float(i) for i in extents['scale'].split(' ')]
        self.xz_max = max([self.s_x / 2, self.s_z / 2])

        if mode == 'all':
            kf_query = query.ALL_KILLFEED
        else:
            kf_query = query.ROUND_KILLFEED

        with Database() as db:
            marine_kf = db.execute(kf_query.format(self.map, 1, id)).fetchall()
        self.marine_kills = np.array(
            [self.coord_to_map(*x['killerPosition'].split(' ')) for x in marine_kf])

        with Database() as db:
            alien_kf = db.execute(kf_query.format(self.map, 2, id)).fetchall()
        self.alien_kills = np.array(
            [self.coord_to_map(*x['killerPosition'].split(' ')) for x in alien_kf])

        self.create()

    def coord_to_map(self, x, y, z):
        # Convert ingame coordinates to map pixels. Formula taken from wonitor code.
        x, y, z = float(x), float(y), float(z)
        image_x = (z - self.o_z) / self.xz_max * self.map_width + self.map_width / 2
        image_y = -(x - self.o_x) / self.xz_max * self.map_height + self.map_height / 2
        return (image_x, image_y)

    def heatmap(self, x, y, s=16, bins=1024):
        # From kill positions, create a 2d histogram, apply gaussian filter, return the matrix
        heatmap, xedges, yedges = np.histogram2d(x, y, bins=bins, range=[[0, 1024], [0, 1024]])
        heatmap = gaussian_filter(heatmap, sigma=s)
        return heatmap.T

    def create(self):
        x1, y1 = self.marine_kills[:, 0], self.marine_kills[:, 1]
        x2, y2 = self.alien_kills[:, 0], self.alien_kills[:, 1]

        img_m = self.heatmap(x1, y1)
        img_a = self.heatmap(x2, y2)

        # Normalize values to [0; 1] range
        img_m = img_m / np.max(img_m)
        img_a = img_a / np.max(img_a)

        # Difference in kills: <0 alien sided spot, >0 marine sided spot, =0 nothing or perfectly balanced
        img = img_m - img_a

        # Normalize values to [-1; 1] range
        intp = interp1d([np.min(img), np.max(img)], [-1, 1])
        img = intp(img)

        # Assume (0,0) contains the baseline value, then substract matrices
        # -because normalize steps shifted the background to some other value rather than 0-
        img = img - img[0][0]

        # Plot with a colormap, and create PNG
        fig = plt.figure(frameon=False, figsize=(10, 10))
        ax = plt.Axes(fig, [0., 0., 1., 1.])
        ax.set_axis_off()

        ax.imshow(img, cmap=cm.RdBu_r, vmin=-1, vmax=1)
        ax.imshow(plt.imread('minimaps/{}.png'.format(self.map)), alpha=0.20)
        fig.add_axes(ax)

        img_io = io.BytesIO()
        fig.savefig(img_io, format='png')
        img_io.seek(0)

        image = Image.open(img_io)
        r, g, b, a = image.split()
        rgb_image = Image.merge('RGB', (r, g, b))

        # Inverted image looks nicer with a black background
        inverted_image = PIL.ImageOps.invert(rgb_image)

        r2, g2, b2 = inverted_image.split()
        final = Image.merge('RGBA', (r2, g2, b2, a))

        final.save(self.map + '.png', format='PNG')
        self.img = io.BytesIO()
        final.save(self.img, format='PNG')
        self.img.seek(0)
        plt.close()


class Round:
    def __init__(self, round_id):
        with Database() as db:
            self.round_info = db.execute(query.ROUND_INFO.format(round_id)).fetchall()[0]
            self.players = db.execute(query.ROUND_PLAYERS.format(round_id)).fetchall()

            self.marines = [x for x in self.players if x['teamNumber'] == 1]
            self.aliens = [x for x in self.players if x['teamNumber'] == 2]
            self.marines.sort(key=lambda x: x['kills'], reverse=True)
            self.aliens.sort(key=lambda x: x['kills'], reverse=True)

            for i, player in enumerate(self.aliens):
                lifeforms = [x['class'] for x in
                             db.execute(query.ROUND_PLAYER_LIFEFORMS.format(round_id, player['steamId'])).fetchall()]
                self.aliens[i]['lifeforms'] = lifeforms

        # Quitters or late joiners, players with < 90% of the game length
        self.quitters_late_joiners = [x for x in self.players if
                                      x['timePlayed'] < self.round_info['roundLength'] * 0.90]

        rd = datetime.datetime.strptime(self.round_info['roundDate'], '%Y-%m-%d %H:%M:%S')
        rd_utc = rd.replace(tzinfo=datetime.timezone.utc)
        self.round_date = rd_utc.astimezone()

        try:
            self.heatmap = Heatmap(self.round_info['mapName'], 'round', round_id)
        except:
            self.heatmap = None

        self.send_embed()

    def send_embed(self):
        marines_str, aliens_str, qlj_str = '', '', ''

        for p in self.marines:
            p_str = f'**{p["playerName"]}** ({p["kills"]}/{p["assists"]}/{p["deaths"]})'
            marines_str += p_str + '\n'

        for p in self.aliens:
            lifeforms_str = ", ".join(p["lifeforms"])
            p_str = f'**{p["playerName"]}** ({p["kills"]}/{p["assists"]}/{p["deaths"]})'
            if lifeforms_str:
                p_str += f' [{lifeforms_str}]'
            aliens_str += p_str + '\n'

        for p in self.quitters_late_joiners:
            time_played = time.strftime("%H:%M:%S", time.gmtime(p["timePlayed"]))
            p_str = f'`[{p["steamId"]}]` **{p["playerName"]}** *(played {time_played})*'
            qlj_str += p_str + '\n'

        color = 0x38b6ff if self.round_info['winningTeam'] == 1 else 0xff8819

        webhook_url = config.WEBHOOK_URL
        webhook_data = {
            "username": "End of Round",
            "avatar_url": "https://phatburn.com/wp-content/uploads/2016/05/too_much_salt_360.jpg",
            "embeds": [
                {
                    "author": {
                        "name": "End of Round"
                    },
                    "title": f'**`#{self.round_info["roundId"]} {self.round_info["mapName"]}`  {time.strftime("%H:%M:%S", time.gmtime(self.round_info["roundLength"]))}**',
                    "color": color,
                    "timestamp": datetime.datetime.now(tz).isoformat(),
                    "description": "Marines win" if self.round_info['winningTeam'] == 1 else "Aliens win",
                    "fields": [
                        {
                            "name": "Marines",
                            "value": marines_str,
                            "inline": True
                        },
                        {
                            "name": "Aliens",
                            "value": aliens_str,
                            "inline": True
                        }
                    ],
                    "footer": {
                        "text": "https://github.com/Tikzz/ns2-roundend-hook",
                        "icon_url": "https://i.imgur.com/J7euM0p.png"
                    }
                }
            ]
        }

        if self.quitters_late_joiners:
            webhook_data['embeds'][0]['fields'].append({
                "name": "Quitters & Late joiners",
                "value": qlj_str
            })

        if self.heatmap:
            heatmap_imgur = config.imgur.upload_image(io=self.heatmap.img)
            webhook_data['embeds'][0]['image'] = {
                "url": heatmap_imgur.link
            }
        requests.post(webhook_url, data=[('payload_json', json.dumps(webhook_data))])


class LastPostedRound:
    def set(self, round_id):
        f = open('last_round_id', 'w')
        f.write(str(round_id))
        f.close()

    def get(self):
        try:
            open('last_round_id', 'r')
        except FileNotFoundError:
            self.set(0)
        finally:
            f = open('last_round_id', 'r')
        last_round_id = f.read()
        f.close()
        return int(last_round_id)


if __name__ == '__main__':
    lpr = LastPostedRound()
    while True:
        with Database() as db:
            last_round = db.execute(query.LAST_ROUND).fetchall()[0]['roundId']

        last_posted_round = lpr.get()

        if last_round > last_posted_round:
            with Database() as db:
                new_rounds = db.execute(query.ROUNDS_GREATER.format(last_posted_round)).fetchall()

            logging.info(f'Found {len(new_rounds)} new rounds.')

            for r in new_rounds:
                round_id = r['roundId']
                logging.info(f'Getting round ID {round_id}')
                Round(round_id)
                lpr.set(round_id)
        else:
            logging.info(f'No new rounds found (LastPosted: {last_posted_round}, LastDB: {last_round})')

        time.sleep(config.CHECK_DELAY)
