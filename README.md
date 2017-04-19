# pleskfm

Tool for interacting with the plesk web interface from the commandline.
The commands attempt to mimic the standard unix commandline tools.



## Example usage:

    plesk -c test mkdir testdir
    plesk -c test empty testdir/tst1.txt
    plesk -c test edit testdir/tst1.txt abcakjsdkhjasdjkhasd
    plesk -c test cat testdir/tst1.txt

    echo abcakjsdkhjasdjkhasd | plesk -c test tee testdir/tst2.txt 
    plesk -c test cat testdir/tst2.txt

    plesk -c test ls testdir

## commands

* ls [-h] [--recurse] dirname
* cat [-h] filename
* tee [-h] filename
* get [-h] filename destination
* put [-h] filename destination
* edit [-h] filename contents
* zip [-h] [-C DIRNAME] zipname [files [files ...]]
* unzip [-h] zipname
* mkdir [-h] dirname
* rmdir [-h] dirname
* rm [-h] [files [files ...]]
* empty [-h] filename
* mv [-h] [-C DIRNAME] files [files ...] destination
* cp [-h] [-C DIRNAME] files [files ...] destination
* du [-h] [-C DIRNAME] [files [files ...]]
* help [-h] [subcommand]


## Configuration

Site configuration is read from ~/.pleskrc.
See `pleskrc.example` for an example config file.


## AUTHOR

Willem Hengeveld <itsme@xs4all.nl>


