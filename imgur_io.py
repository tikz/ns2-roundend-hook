import pyimgur
from base64 import b64encode

class Imgur(pyimgur.Imgur):
    def upload_image(self, path=None, url=None, title=None, description=None, album=None, io=None):

        if bool(path) == bool(url) == bool(io):
            raise LookupError("Either path, url or io must be given.")
        if path:
            with open(path, 'rb') as image_file:
                binary_data = image_file.read()
                image = b64encode(binary_data)
        if io:
            binary_data = io.read()
            image = b64encode(binary_data)
        else:
            image = url

        payload = {'album_id': album, 'image': image,
                   'title': title, 'description': description}

        resp = self._send_request(self._base_url + "/3/image",
                                  params=payload, method='POST')

        resp['title'] = title
        resp['description'] = description
        if album is not None:
            resp['album'] = (pyimgur.Album({'id': album}, self, False) if not
                             isinstance(album, pyimgur.Album) else album)
        return pyimgur.Image(resp, self)