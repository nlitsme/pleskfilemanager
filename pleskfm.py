"""

Tool for interacting with the plesk web interface from the commandline.

Site configuration is read from ~/.pleskrc.

Usage:

    plesk -c test mkdir testdir
    plesk -c test empty testdir/tst1.txt
    plesk -c test edit testdir/tst1.txt abcakjsdkhjasdjkhasd
    plesk -c test cat testdir/tst1.txt

    echo abcakjsdkhjasdjkhasd | plesk -c test tee testdir/tst2.txt
    plesk -c test cat testdir/tst2.txt


AUTHOR: willem Hengeveld <itsme@xs4all.nl>

TODO:
  * remember session cookie, so we don't have to login each time.
  * use task-progress while waiting for results.
  * add parallel recurse option to ls
  * instead of specifying '-C' and disallowing relative paths.
    split the action in several actions, each with their own directory.
    so: "cp dir1/file1 dir1/file2 dir2/file3  dst"
    will become:
       "cp -C dir1 file1 file2 dst"
       and
       "cp -C dir2 file3 dst"

"""
import html.parser
import datetime
import os.path
import os
import sys
import asyncio
import aiohttp

if sys.version_info[0] == 3:
    unicode = str


class TokenFilter(html.parser.HTMLParser):
    """
    Parses html, stores the forgery_protection_token found in self.token.
        <meta name="forgery_protection_token" id="forgery_protection_token" content="3b55c0fb579094ccdf0d1e84ae183062">
    """
    def __init__(self):
        super().__init__()
        self.token = None

    def checkmeta(self, attrs):
        d = dict(attrs)
        if d.get('name')=='forgery_protection_token':
            self.token = d['content']

    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            self.checkmeta(attrs)

    def handle_startendtag(self, tag, attrs):
        if tag == 'meta':
            self.checkmeta(attrs)


def ExtractToken(html):
    parser = TokenFilter()
    parser.feed(html)
    parser.close()
    return parser.token


class ErrorFilter(html.parser.HTMLParser):
    """
    Parses html, extracts the error message from <div class='msgbox msg-error'>
    """
    def __init__(self):
        super().__init__()
        self.stack = []
        self.level = 0
        self.errormsg = ""

    def handle_starttag(self, tag, attrs):
        if tag in ("meta", "input", "br", "link", "img", "hr"):
            return self.handle_startendtag(tag, attrs)
        self.stack.append(tag)

        if tag == 'div':
            d = dict(attrs)
            cls = d.get("class", "")
            if cls.find('msg-error')>0:
                self.level = len(self.stack)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if self.level == len(self.stack):
            self.level = 0
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            for i, e in reversed(list(enumerate(self.stack))):
                if e==tag:
                    print("missing end tag for:", self.stack[i+1:], "closing", self.stack[i:i+1])
                    while len(self.stack)>i:
                        self.stack.pop()
                    return
            print("could not find start tag for:", tag, "in", self.stack)

    def handle_data(self, data):
        if self.level:
            if self.errormsg:
                self.errormsg += " "
            self.errormsg += data


def ExtractError(html):
    parser = ErrorFilter()
    parser.feed(html)
    parser.close()
    return parser.errormsg.strip()


class WebHosting:
    """
    Has methods for each smb/file-manager function.

    Always call 'start' as the first method.
    """
    def __init__(self, loop, baseurl):
        self.baseurl = baseurl
        self.loop = loop
        self.client = None

    def __del__(self):
        if self.client:
            self.client.close()

    def post(self, path, **kwargs):
        return self.client.post(self.baseurl+path, **kwargs)

    def get(self, path, **kwargs):
        return self.client.get(self.baseurl+path, **kwargs)

    async def start(self, args):
        """
        optionally logs in, obtains the csfr token.
        """
        conn = aiohttp.TCPConnector(verify_ssl=not args.ignoresslerrors)
        self.client = aiohttp.ClientSession(loop=self.loop, connector=conn)

        if args.username:
            resp = await self.login(args.username, args.password)
            # todo: save either PLESKSESSID  or PHPSESSID
            resp.close()

        resp = await self.gettoken()
        self.token = ExtractToken(await resp.text())
        resp.close()

    def gettoken(self):
        return self.get("smb/")

    def makeform(self, *args, **kwargs):
        """
        create a form from the arguments:
           * first a list of optional file arguments
           * followed by a list of keyword args.
        """
        kwargs['forgery_protection_token'] = self.token
        for i, arg in enumerate(args):
            kwargs['ids[%d]' % i] = arg
        return kwargs

    def login(self, user, pw):
        return self.post("login_up.php3", data={"login_name":user, "passwd":pw, "locale_id":"default"})

    def listdir(self, dirname):
        # changes the current dir
        # returns json: { additional:{ operations:[] }, localte:{ ... }, pager:{ ... }, pathbar:{ ... }, state: { currentDir }, data: [ { filePerms:"...", formattedSize:..., isDirectory, name, size, type, user, actions:[ { href, name, title } ] } ] }
        return self.get("smb/file-manager/list-data", params={"currentDir":dirname})

    async def download(self, dirname, filename, fh):
        resp = await self.get("smb/file-manager/download", params={"currentDir":dirname, "file":filename})
        if not resp.headers.get('Content-Disposition') and resp.headers.get('Content-Type').startswith('text/html'):
            error = ExtractError(await resp.text())
            resp.close()

            # note: sometimes the error will be '<h1>internal error</h1>
            raise Exception(error)

        while True:
            chunk = await resp.content.read(0x10000)
            if not chunk:
                break
            fh.write(chunk)
        resp.close()

    def delfiles(self, filelist):
        # note: list-data apparently conveys a notion of 'current-directory' to the server.
        form = self.makeform(*filelist)
        return self.post("smb/file-manager/delete", data=form)

    def calcsize(self, filelist):
        # returns json dict:  { fileSizes:{ filename:size-string }, statusMessages: { content: "Selectiegrootte: ...", "status":"info" } }
        for i, fn in enumerate(filelist):
            if fn.find('/')>=0:
                raise Exception("calcsize does not operate on subdirectories")
        form = self.makeform(*filelist)
        return self.post("smb/file-manager/calculate-size", data=form)

    def makezip(self, zipname, filelist):
        # returns json dict:  { message: "%%archive%% is gemaakt", "status":"success" }
        form = self.makeform(*filelist, archiveName=zipname)
        return self.post("smb/file-manager/create-archive", data=form)

    def unzip(self, zipname):
        form = self.makeform(zipname)
        return self.post("smb/file-manager/extract-archive", data=form, params={'overwrite':'true'})

    def mkdir(self, dirname):
        form = self.makeform(newDirectoryName=dirname)
        return self.post("smb/file-manager/create-directory", data=form)

    def createemptyfile(self, filename):
        # todo: figure out what the htmlTemplate is for.
        form = self.makeform(newFileName=filename, htmlTemplate=False)
        return self.post("smb/file-manager/create-file", data=form)

    def rename(self, oldname, newname):
        form = self.makeform(oldname, newFileName=newname)
        return self.post("smb/file-manager/rename", data=form)

    def copy(self, filelist, destination):
        form = self.makeform(*filelist)
        # note: aiohttp inconsistency: in form i can use booleans, in params i can't
        return self.post("smb/file-manager/copy-files", data=form, params={"destinationDir":destination, "overwrite":'false'})

    def move(self, filelist, destination):
        form = self.makeform(*filelist)
        return self.post("smb/file-manager/move-files", data=form, params={"destinationDir":destination, "overwrite":'false'})

    async def editfile(self, dirname, filename, data):
        form = self.makeform(eol='LF', saveCodepage='UTF-8', loadCodepage='UTF-8', code=data)
        resp = await self.post("smb/file-manager/edit", data=form, params={"currentDir":dirname, 'file':filename})

        error = ExtractError(await resp.text())
        resp.close()

        if error:
            raise Exception(error)

    def upload(self, fh, filename):
        form = aiohttp.FormData(self.makeform())
        form.add_field(filename, fh, filename=filename, content_type='application/octet-stream')
        return self.post("smb/file-manager/upload", data=form)

######################################################################################

# -- cmdline ---            -- async func --     -- host method --                   -- url --
# ls                        listfiles            listdir(dirname)                     list-data
# cat/get                   downloadfile         download(dirname, filename)          download
# rm                        delfiles             delfiles(list)                       delete
# du                        calcsize             calcsize(list)                       calculate-size
# zip                       makezip              makezip(name, list)                  create-archive
# unzip                     unzip                unzip(name)                          extract-archive
# mkdir                     createdir            mkdir(name)                          create-directory
# empty                     emptyfile            createemptyfile(name)                create-file
#                                                rename(old, new)                     rename
# cp                        copyfiles            copy(list, dst)                      copy-files
# mv                        movefiles            move(list, dst)                      move-files
# put                       uploadfile           upload(fh, filename)                 upload
# edit                      editfile             editfile(dirname, filename, data)    edit


async def listfiles(host, dirname, args):
    resp = await host.listdir(dirname)
    info = await resp.json()

    if args.verbose:
        print(info)

    resp.close()
    if info.get('status') == 'error':
        if args.ignoreerror:
            print("ERROR", dirname, info.get('message'))
            return
        raise Exception(info.get('message'))

    print("%s:" % dirname)
    for finfo in info["data"]:
        perms = "d" if finfo.get("isDirectory") else "-"
        perms += finfo["filePerms"].replace(" ", "")

        tstr = datetime.datetime.fromtimestamp(int(finfo["modificationTimestamp"]))
        print("%-10s  %-12s %-12s %12s  %s  %s" % (perms, finfo["user"], finfo["group"], finfo["size"], tstr.strftime("%Y-%m-%d %H:%M:%S"), finfo["name"]))
    print()

    if args.recurse:
        for finfo in info["data"]:
            if finfo.get("isDirectory") and finfo["name"] not in ("..", "."):
                await listfiles(host, os.path.join(dirname, finfo["name"]), args)


async def downloadfile(host, srcfilename, dst):
    dirname, srcfilename = os.path.split(srcfilename)

    if type(dst) not in (bytes, str, unicode):
        fh = dst
    elif not dst or dst == '-':
        fh = sys.stdout.buffer
    elif os.path.isdir(dst):
        dst = os.path.join(dst, srcfilename)
        fh = open(dst, "wb")
    else:
        fh = open(dst, "wb")

    sys.stdout.flush()

    await host.download(dirname, srcfilename, fh)


async def uploadfile(host, srcfilename, dstname):
    dirname, dstfilename = os.path.split(dstname)
    if dirname:
        resp = await host.listdir(dirname)
        info = await resp.json()
        resp.close()
        if info.get('status') == 'error':
            raise Exception(info.get('message'))

        newdir = info.get('state', dict()).get('currentDir')
        if newdir != dirname:
            print("Failed to change to '%s':  curdir='%s'" % (dirname, newdir))
            raise Exception("cannot change to directory")

    if type(srcfilename) not in (bytes, str, unicode):
        fh = srcfilename
    if srcfilename == '-':
        fh = sys.stdin.buffer
    else:
        fh = open(srcfilename, "rb")

    resp = await host.upload(fh, dstfilename)
    res = await resp.text()
    resp.close()


async def makezip(host, dirname, zipname, files):
    if dirname and dirname not in ('', '/'):
        resp = await host.listdir(dirname)
        info = await resp.json()
        resp.close()
        if info.get("status") == "error":
            raise Exception(info["message"])

    # server will add '.zip'
    zipname = zipname.replace('.zip', '')
    if zipname.find('/')>=0:
        raise Exception("use -C to specify where the zipfile goes")
    resp = await host.makezip(zipname, files)
    info = await resp.json()
    resp.close()
    if info.get('status')=='fail':
        raise Exception(info.get('message'))


async def unzip(host, zipname):
    resp = await host.unzip(zipname)
    info = await resp.json()
    resp.close()
    msgs = info.get('statusMessages', [])
    if msgs and msgs[0].get('status')=='error':
        raise Exception(msgs[0].get('content'))


async def removedir(host, dirname):
    # note: this is always recursively, and always succeeds
    resp = await host.delfiles([dirname])
    print(await resp.text())
    resp.close()


async def createdir(host, dirname):
    basepath, dirname = os.path.split(dirname.rstrip('/'))
    if basepath not in ('', '/'):
        resp = await host.listdir(basepath)
        info = await resp.json()
        resp.close()
        if info.get("status") == "error":
            raise Exception(info["message"])

    resp = await host.mkdir(dirname)
    info = await resp.json()
    resp.close()
    if info.get("status") == "error":
        raise Exception(info["message"])


async def delfiles(host, files):
    resp = await host.delfiles(files)
    print(await resp.text())
    resp.close()


async def emptyfile(host, filename):
    dirname, filename = os.path.split(filename)
    if dirname not in ('', '/'):
        resp = await host.listdir(dirname)
        info = await resp.json()
        resp.close()
        if info.get("status") == "error":
            raise Exception(info["message"])

    resp = await host.createemptyfile(filename)
    info = await resp.json()
    resp.close()
    if info.get("status") == "error":
        raise Exception(info["message"])


async def copyfiles(host, dirname, files, destination):
    if dirname and dirname not in ('', '/'):
        resp = await host.listdir(dirname)
        info = await resp.json()
        resp.close()
        if info.get("status") == "error":
            raise Exception(info["message"])

    resp = await host.copy(files, destination)
    info = await resp.json()
    resp.close()
    msgs = info.get('statusMessages', [])
    if msgs and msgs[0].get('status')=='error':
        raise Exception(msgs[0].get('content'))


async def movefiles(host, dirname, files, destination):
    if dirname and dirname not in ('', '/'):
        resp = await host.listdir(dirname)
        info = await resp.json()
        resp.close()
        if info.get("status") == "error":
            raise Exception(info["message"])

    resp = await host.move(files, destination)
    info = await resp.json()
    resp.close()
    msgs = info.get('statusMessages', [])
    if msgs and msgs[0].get('status')=='error':
        raise Exception(msgs[0].get('content'))


async def calcsize(host, dirname, files):
    if dirname and dirname not in ('', '/'):
        resp = await host.listdir(dirname)
        info = await resp.json()
        resp.close()
        if info.get("status") == "error":
            raise Exception(info["message"])

    resp = await host.calcsize(files)
    info = await resp.json()
    resp.close()
    msgs = info.get('statusMessages', [])
    if msgs and msgs[0].get('status')=='error':
        raise Exception(msgs[0].get('content'))

    print(msgs[0].get('content'))


async def editfile(host, filename, contents):
    dirname, filename = os.path.split(filename)
    await host.editfile(dirname, filename, contents)


async def dologin(host, args):
    await host.start(args)

#################################

def makeparser():
    """
    Create the commandline parser.
    """
    import argparse
    parser = argparse.ArgumentParser(description='plesk file utility')
    parser.add_argument('--config', '-c', type=str, help='configuration to use')
    parser.add_argument('--baseurl', help='plesk base url')
    parser.add_argument('--ignoresslerrors', '-k', action='store_true', help='Ignore ssl certificate errors')
    parser.add_argument('--username', '-u', help='username for login')
    parser.add_argument('--password', '-p', help='password for login')
    parser.add_argument('--verbose', '-v', action='store_true', help='print results from web requests')

    sub = parser.add_subparsers(dest='command')

    ls = sub.add_parser('ls', help='list files')
    ls.add_argument('--recurse', '-r', action='store_true', help='recursively list directories')
    ls.add_argument('--ignoreerror', '-c', action='store_true', help='continue after error')
    ls.add_argument('dirname', type=str, help='which directory to list')

    cat = sub.add_parser('cat', help='print remote file contents to stdout')
    cat.add_argument('filename', help='which file')

    tee = sub.add_parser('tee', help='save stdin to a remote file')
    tee.add_argument('filename', help='which file')

    cat = sub.add_parser('get', help='copy remote file')
    cat.add_argument('filename', help='which remote file')
    cat.add_argument('destination', help='where to store locally', default='.')

    put = sub.add_parser('put', help='upload file')
    put.add_argument('filename', help='which local file')
    put.add_argument('destination', help='where to store remotely')

    edit = sub.add_parser('edit', help='edit file contents')
    edit.add_argument('filename', help='which file')
    edit.add_argument('contents', help='the new contents')

    azip = sub.add_parser('zip', help='archive files')
    azip.add_argument('--dirname', '-C', help='the directory containing the requested files')
    azip.add_argument('zipname', help='name of the zip archive')
    azip.add_argument('files', nargs='*', help='which files to zip')

    unzip = sub.add_parser('unzip', help='unarchive files')
    unzip.add_argument('zipname', help='name of the zip archive')

    mkdir = sub.add_parser('mkdir', help='create directory')
    mkdir.add_argument('dirname')

    rmdir = sub.add_parser('rmdir', help='delete directory')
    rmdir.add_argument('dirname')

    delfiles = sub.add_parser('rm', help='delete files')
    delfiles.add_argument('files', nargs='*')

    emptyfile = sub.add_parser('empty', help='create empty file')
    emptyfile.add_argument('filename')

    movefiles = sub.add_parser('mv', help='move files, note: the destination must be an absolute path')
    movefiles .add_argument('--dirname', '-C', help='the directory containing the requested files')
    movefiles.add_argument('files', nargs='+')

    copyfiles = sub.add_parser('cp', help='copy files, note: the destination must be an absolute path')
    copyfiles.add_argument('--dirname', '-C', help='the directory containing the requested files')
    copyfiles.add_argument('files', nargs='+')

    calcsize = sub.add_parser('du', help='calc size of filelist')
    calcsize.add_argument('--dirname', '-C', help='the directory containing the requested files')
    calcsize.add_argument('files', nargs='*')

    help = sub.add_parser('help', help='verbose usage')
    help.add_argument('subcommand', nargs='?')

    # keep the available choices for later use in 'help'`
    parser.subparsers = sub.choices

    return parser


def loadconfig():
    """
    reads the configuration file
    """
    homedir = os.environ['HOME']

    import configparser
    config = configparser.ConfigParser()

    config.read(os.path.join(homedir, ".pleskrc"))

    return config


def applyconfig(args, config):
    """
    Take the section specified in the config commandline option,
    or the one named as the default in the config file,
    or just take the first section.

    and set defaults for several commandline options.
    """
    cfgname = args.config
    if config.sections() and not cfgname:
        cfgname = config.sections()[0]
    section = config[cfgname]

    if not args.username: args.username = section.get('username')
    if not args.baseurl: args.baseurl = section.get('baseurl')
    if not args.password: args.password = section.get('password')
    if not args.ignoresslerrors: args.ignoresslerrors=section.get('ignoresslerrors')


def main():
    loop = asyncio.get_event_loop()
    config = loadconfig()
    parser = makeparser()
    args = parser.parse_args()
    applyconfig(args, config)

    host = WebHosting(loop, args.baseurl)

    tasks = []

    if args.command == 'ls':
        tasks.append(listfiles(host, args.dirname, args))
    elif args.command == 'cat':
        tasks.append(downloadfile(host, args.filename, "-"))
    elif args.command == 'tee':
        tasks.append(uploadfile(host, "-", args.filename))
    elif args.command == 'get':
        tasks.append(downloadfile(host, args.filename, args.destination))
    elif args.command == 'put':
        tasks.append(uploadfile(host, args.filename, os.path.join(args.destination, args.filename)))
    elif args.command == 'edit':
        tasks.append(editfile(host, args.filename, args.contents))
    elif args.command == 'zip':
        tasks.append(makezip(host, args.dirname, args.zipname, args.files))
    elif args.command == 'unzip':
        tasks.append(unzip(host, args.zipname))
    elif args.command == 'rmdir':
        tasks.append(removedir(host, args.dirname))
    elif args.command == 'mkdir':
        tasks.append(createdir(host, args.dirname))
    elif args.command == 'rm':
        tasks.append(delfiles(host, args.files))
    elif args.command == 'empty':
        tasks.append(emptyfile(host, args.filename))
    elif args.command == 'cp':
        tasks.append(copyfiles(host, args.dirname, args.files[:-1], args.files[-1]))
    elif args.command == 'mv':
        tasks.append(movefiles(host, args.dirname, args.files[:-1], args.files[-1]))
    elif args.command == 'du':
        tasks.append(calcsize(host, args.dirname, args.files))
    elif args.command == 'help':
        if args.subcommand:
            p = parser.subparsers.get(args.subcommand)
            if p:
                p.print_help()
                sys,exit(0)

        parser.print_help()
        print()
        for p in parser.subparsers.values():
            p.print_usage()
        print()
        sys.exit(0)
    else:
        parser.print_usage()
        sys.exit(1)

    loop.run_until_complete(dologin(host, args))

    try:
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks))
    except Exception as e:
        print("ERROR", e)
        sys.exit(1)


if __name__ == '__main__':
    main()
